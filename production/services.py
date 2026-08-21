from django.db import transaction
from django.utils import timezone

from production.models import PartnerJob, WeighingRecord


def _generate_partner_job_code(order_id: int) -> str:
    ts = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"JOB-{order_id}-{ts}"


def _validate_service_execution_order(*, order, service_execution):
    """
    Garantit qu'un PartnerJob ne peut pas être rattaché à une
    ServiceExecution appartenant à une autre commande.

    Pendant la phase de strangulation, service_execution peut être None.
    """
    if service_execution is None:
        return

    execution_order_id = getattr(
        service_execution,
        "order_id",
        None,
    )

    order_id = getattr(
        order,
        "id",
        None,
    )

    if not order_id:
        raise ValueError(
            "Impossible de créer un PartnerJob : commande non persistée."
        )

    if execution_order_id != order_id:
        raise ValueError(
            "ServiceExecution incompatible : "
            "l'exécution de service et le PartnerJob doivent appartenir "
            "à la même commande."
        )


@transaction.atomic
def create_partner_job(
    *,
    order,
    partner,
    service_execution=None,
    notes="",
):
    """
    Crée un PartnerJob partenaire.

    Compatibilité :
    - legacy : order + partner restent suffisants ;
    - multiservices : service_execution peut être fourni.

    Aucune ServiceExecution n'est créée implicitement ici.
    """
    _validate_service_execution_order(
        order=order,
        service_execution=service_execution,
    )

    job = PartnerJob.objects.create(
        code=_generate_partner_job_code(order.id),
        order=order,
        service_execution=service_execution,
        partner=partner,
        status="awaiting_reception",
        notes=notes or "",
    )

    return job


@transaction.atomic
def mark_partner_job_received(*, partner_job, notes=""):
    if partner_job.status not in {"awaiting_reception", "issue"}:
        raise ValueError(
            f"Impossible de réceptionner un job au statut {partner_job.status}"
        )

    partner_job.status = "received"
    partner_job.received_at = timezone.now()

    if notes:
        partner_job.notes = (
            (partner_job.notes + "\n" + notes).strip()
            if partner_job.notes
            else notes
        )

    partner_job.save(
        update_fields=[
            "status",
            "received_at",
            "notes",
            "updated_at",
        ]
    )

    return partner_job


@transaction.atomic
def mark_partner_job_processing(*, partner_job, notes=""):
    if partner_job.status not in {"received", "weighed", "confirmed"}:
        raise ValueError(
            f"Impossible de lancer le traitement au statut {partner_job.status}"
        )

    partner_job.status = "processing"
    partner_job.processing_started_at = timezone.now()

    if notes:
        partner_job.notes = (
            (partner_job.notes + "\n" + notes).strip()
            if partner_job.notes
            else notes
        )

    partner_job.save(
        update_fields=[
            "status",
            "processing_started_at",
            "notes",
            "updated_at",
        ]
    )

    return partner_job


@transaction.atomic
def mark_partner_job_ready(*, partner_job, notes=""):
    if partner_job.status not in {"processing", "confirmed", "weighed"}:
        raise ValueError(
            f"Impossible de passer prêt au statut {partner_job.status}"
        )

    partner_job.status = "ready"
    partner_job.ready_at = timezone.now()

    if notes:
        partner_job.notes = (
            (partner_job.notes + "\n" + notes).strip()
            if partner_job.notes
            else notes
        )

    partner_job.save(
        update_fields=[
            "status",
            "ready_at",
            "notes",
            "updated_at",
        ]
    )

    return partner_job


@transaction.atomic
def handover_partner_job(*, partner_job, notes=""):
    if partner_job.status != "ready":
        raise ValueError(
            "Impossible de remettre au livreur un job au statut "
            f"{partner_job.status}"
        )

    partner_job.status = "handed_over"
    partner_job.handed_over_at = timezone.now()

    if notes:
        partner_job.notes = (
            (partner_job.notes + "\n" + notes).strip()
            if partner_job.notes
            else notes
        )

    partner_job.save(
        update_fields=[
            "status",
            "handed_over_at",
            "notes",
            "updated_at",
        ]
    )

    if partner_job.service_execution_id is not None:
        from services.services import complete_service_execution_if_ready

        complete_service_execution_if_ready(
            service_execution=partner_job.service_execution,
            note=(
                "ServiceExecution réévaluée après remise au livreur "
                f"du PartnerJob {partner_job.code}."
            ),
        )

    return partner_job


@transaction.atomic
def record_weighing(
    *,
    order,
    net_weight,
    weighing_stage,
    partner_job=None,
    mission=None,
    service_execution=None,
    gross_weight=None,
    performed_by_role="driver",
    unit="kg",
    notes="",
):
    # ---------------------------------------------------------
    # Garde-fou : un PartnerJob annulé est opérationnellement fermé
    # ---------------------------------------------------------
    if (
        partner_job is not None
        and partner_job.status == "canceled"
    ):
        raise ValueError(
            "Impossible d'enregistrer une pesée sur un PartnerJob annulé."
        )

    # ---------------------------------------------------------
    # Résolution canonique de la ServiceExecution
    # ---------------------------------------------------------
    # Une pesée peut recevoir l'exécution :
    # - explicitement ;
    # - via le PartnerJob ;
    # - via la Mission.
    #
    # Toutes les sources présentes doivent être cohérentes.
    candidate_execution_ids = set()

    if service_execution is not None:
        candidate_execution_ids.add(service_execution.id)

    if (
        partner_job is not None
        and partner_job.service_execution_id is not None
    ):
        candidate_execution_ids.add(partner_job.service_execution_id)

    if (
        mission is not None
        and mission.service_execution_id is not None
    ):
        candidate_execution_ids.add(mission.service_execution_id)

    if len(candidate_execution_ids) > 1:
        raise ValueError(
            "ServiceExecution incompatible : "
            "les rattachements de la pesée désignent "
            "plusieurs exécutions de service."
        )

    resolved_service_execution = service_execution

    if (
        resolved_service_execution is None
        and partner_job is not None
        and partner_job.service_execution_id is not None
    ):
        resolved_service_execution = partner_job.service_execution

    if (
        resolved_service_execution is None
        and mission is not None
        and mission.service_execution_id is not None
    ):
        resolved_service_execution = mission.service_execution

    _validate_service_execution_order(
        order=order,
        service_execution=resolved_service_execution,
    )

    if (
        partner_job is not None
        and partner_job.order_id != order.id
    ):
        raise ValueError(
            "PartnerJob incompatible : "
            "le PartnerJob et la pesée doivent appartenir "
            "à la même commande."
        )

    if (
        mission is not None
        and mission.order_id != order.id
    ):
        raise ValueError(
            "Mission incompatible : "
            "la Mission et la pesée doivent appartenir "
            "à la même commande."
        )

    record = WeighingRecord.objects.create(
        order=order,
        service_execution=resolved_service_execution,
        partner_job=partner_job,
        mission=mission,
        performed_by_role=performed_by_role,
        weighing_stage=weighing_stage,
        gross_weight=gross_weight,
        net_weight=net_weight,
        unit=unit,
        notes=notes or "",
    )

    if (
        partner_job
        and partner_job.status in {"awaiting_reception", "received"}
    ):
        partner_job.status = "weighed"
        partner_job.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

    if (
        record.weighing_stage == "final_validation"
        and record.service_execution_id is not None
    ):
        from services.services import complete_service_execution_if_ready

        complete_service_execution_if_ready(
            service_execution=record.service_execution,
            note=(
                "ServiceExecution réévaluée après pesée "
                f"de validation finale #{record.id}."
            ),
        )

    return record


@transaction.atomic
def cancel_partner_job(
    *,
    partner_job,
    reason="",
    notes="",
):
    """
    Annule un PartnerJob partenaire.

    Contrat FAGNI — Lot A :
    - handed_over : terminal, annulation interdite ;
    - canceled : opération idempotente ;
    - les autres états restent annulables ;
    - reason est obligatoire lors de la première annulation ;
    - canceled_at est renseigné une seule fois ;
    - l'historique des notes existantes est conservé ;
    - aucune ServiceExecution n'est modifiée ici.
    """

    # Idempotence nécessaire aux retries/orchestrations.
    if partner_job.status == "canceled":
        return partner_job

    if partner_job.status == "handed_over":
        raise ValueError(
            "Impossible d'annuler un PartnerJob déjà remis au livreur "
            "(statut handed_over)."
        )

    reason = str(reason or "").strip()

    if not reason:
        raise ValueError(
            "Le motif d'annulation (reason) est obligatoire "
            "pour garantir l'auditabilité."
        )

    extra_notes = str(notes or "").strip()

    partner_job.status = "canceled"

    if partner_job.canceled_at is None:
        partner_job.canceled_at = timezone.now()

    cancellation_note = f"ANNULATION: {reason}"

    if extra_notes:
        cancellation_note = (
            f"{cancellation_note}\n"
            f"{extra_notes}"
        )

    if partner_job.notes:
        partner_job.notes = (
            f"{partner_job.notes.rstrip()}\n"
            f"{cancellation_note}"
        )
    else:
        partner_job.notes = cancellation_note

    partner_job.save(
        update_fields=[
            "status",
            "canceled_at",
            "notes",
            "updated_at",
        ]
    )

    return partner_job
