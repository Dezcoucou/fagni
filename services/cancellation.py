from django.db import transaction
from django.utils import timezone


class CommercialOrderCancellationError(ValueError):
    """Erreur contractuelle d'annulation commerciale FAGNI."""


@transaction.atomic
def cancel_commercial_order(
    *,
    order,
    reason="",
    notes="",
):
    """
    Porte canonique d'annulation commerciale d'une commande FAGNI.

    Contrat Lot A :
    - reason obligatoire ;
    - opération idempotente si Order est déjà canceled ;
    - Order done non annulable ;
    - une ServiceExecution completed interdit l'annulation ;
    - un PartnerJob handed_over interdit l'annulation ;
    - les Missions completed/failed restent historiques ;
    - les Missions actives sont annulées ;
    - les PartnerJob actifs sont annulés ;
    - les ServiceExecution actives sont annulées ;
    - Order.status devient canceled sans appeler Order.save().
    """

    if order is None or getattr(order, "pk", None) is None:
        raise CommercialOrderCancellationError(
            "Une commande persistée est requise."
        )

    reason = str(reason or "").strip()
    extra_notes = str(notes or "").strip()

    if not reason:
        raise CommercialOrderCancellationError(
            "Le motif d'annulation (reason) est obligatoire."
        )

    Order = order.__class__

    locked_order = (
        Order.objects
        .select_for_update()
        .get(pk=order.pk)
    )

    if locked_order.status == "canceled":
        return {
            "canceled": True,
            "already_canceled": True,
            "service_executions_canceled": 0,
            "missions_canceled": 0,
            "partner_jobs_canceled": 0,
            "delivery_legs_canceled": 0,
        }

    if locked_order.status == "done":
        raise CommercialOrderCancellationError(
            "Impossible d'annuler une commande déjà terminée."
        )

    from services.models import ServiceExecution

    executions = (
        ServiceExecution.objects
        .select_for_update()
        .filter(order=locked_order)
        .order_by("sequence_index", "id")
    )

    if executions.filter(
        status=ServiceExecution.STATUS_COMPLETED
    ).exists():
        raise CommercialOrderCancellationError(
            "Impossible d'annuler cette commande : "
            "au moins une ServiceExecution est déjà complétée."
        )

    if executions.filter(
        partner_jobs__status="handed_over"
    ).exists():
        raise CommercialOrderCancellationError(
            "Impossible d'annuler cette commande : "
            "une prestation a déjà été remise au livreur."
        )

    from logistics.services import cancel_mission
    from production.services import cancel_partner_job
    from services.services import cancel_service_execution

    canceled_executions = 0
    canceled_missions = 0
    canceled_partner_jobs = 0

    terminal_execution_statuses = {
        ServiceExecution.STATUS_COMPLETED,
        ServiceExecution.STATUS_CANCELED,
        ServiceExecution.STATUS_FAILED,
    }

    for execution in executions:
        if execution.status in terminal_execution_statuses:
            continue

        for mission in execution.missions.exclude(
            status__in={
                "completed",
                "failed",
                "canceled",
            }
        ):
            cancel_mission(
                mission=mission,
                reason=(
                    f"Annulation commande #{locked_order.pk} : "
                    f"{reason}"
                ),
                notes=extra_notes,
            )
            canceled_missions += 1

        for partner_job in execution.partner_jobs.exclude(
            status__in={
                "handed_over",
                "canceled",
            }
        ):
            cancel_partner_job(
                partner_job=partner_job,
                reason=(
                    f"Annulation commande #{locked_order.pk} : "
                    f"{reason}"
                ),
                notes=extra_notes,
            )
            canceled_partner_jobs += 1

        cancel_service_execution(
            service_execution=execution,
            note=(
                f"Annulation commande #{locked_order.pk} : "
                f"{reason}"
            ),
        )
        canceled_executions += 1

    # ---------------------------------------------------------
    # CASCADE DELIVERYLEG
    # ---------------------------------------------------------
    # Order / ServiceExecution / Mission cohabitent encore avec
    # les DeliveryLeg legacy pendant le strangler.
    #
    # Contrat FAGNI :
    # - un leg DONE reste un historique opérationnel intangible ;
    # - un leg ayant déjà généré un payout reste intangible ;
    # - un leg déjà CANCELED reste inchangé ;
    # - tout autre leg actif est annulé ;
    # - les montants d'un leg annulé sont neutralisés à zéro.
    #
    # IMPORTANT :
    # DeliveryLeg ne possède pas de champ updated_at.
    # ---------------------------------------------------------
    from decimal import Decimal

    from orders.models import DeliveryLeg
    from wallets.models import WalletTransaction

    canceled_delivery_legs = 0

    delivery_legs = list(
        DeliveryLeg.objects
        .select_for_update()
        .filter(order=locked_order)
        .order_by("id")
    )

    paid_leg_ids = set(
        WalletTransaction.objects.filter(
            order_id=locked_order.pk,
            leg_id__isnull=False,
            type="payout",
            direction="in",
        ).values_list("leg_id", flat=True)
    )

    for leg in delivery_legs:
        current_status = (leg.status or "").strip().lower()

        # Historique opérationnel terminé : jamais rétrograder.
        if current_status == "done":
            continue

        # Historique financier : jamais modifier une jambe payée.
        if leg.pk in paid_leg_ids:
            continue

        # Idempotence.
        if current_status == "canceled":
            continue

        leg.status = "canceled"
        leg.client_fee_share = Decimal("0")
        leg.driver_amount = Decimal("0")
        leg.fagni_margin = Decimal("0")

        leg.save(
            update_fields=[
                "status",
                "client_fee_share",
                "driver_amount",
                "fagni_margin",
            ]
        )

        canceled_delivery_legs += 1

    cancellation_note = f"ANNULATION: {reason}"

    if extra_notes:
        cancellation_note += f"\n{extra_notes}"

    if locked_order.notes:
        new_notes = (
            f"{locked_order.notes.rstrip()}\n"
            f"{cancellation_note}"
        )
    else:
        new_notes = cancellation_note

    Order.objects.filter(pk=locked_order.pk).update(
        status="canceled",
        notes=new_notes,
        updated_at=timezone.now(),
    )

    order.status = "canceled"
    order.notes = new_notes

    return {
        "canceled": True,
        "already_canceled": False,
        "service_executions_canceled": canceled_executions,
        "missions_canceled": canceled_missions,
        "partner_jobs_canceled": canceled_partner_jobs,
        "delivery_legs_canceled": canceled_delivery_legs,
    }
