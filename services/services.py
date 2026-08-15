from django.db import transaction
from django.utils import timezone

from services.models import (
    ServiceExecution,
    ServiceExecutionItem,
)



SERVICE_SNAPSHOT_REQUIREMENT_FIELDS = (
    "requires_partner",
    "requires_logistics",
    "requires_weighing",
    "requires_appointment",
    "requires_quote",
    "requires_asset",
    "requires_otp",
    "requires_signature",
)


def _build_service_execution_snapshot(*, service):
    """
    Construit la photographie minimale et durable du Service au moment
    de la création d'une ServiceExecution.

    Le snapshot ne remplace pas la FK Service :
    il protège l'historique opérationnel contre les modifications
    futures du catalogue.
    """
    category = service.category

    return {
        "service_id": service.id,
        "code": service.code,
        "name": service.name,
        "category": (
            {
                "id": category.id,
                "code": category.code,
                "name": category.name,
            }
            if category is not None
            else None
        ),
        "primary_engine": service.primary_engine,
        "pricing_mode": service.pricing_mode,
        "default_sla_hours": service.default_sla_hours,
        "requirements": {
            field_name: getattr(service, field_name)
            for field_name in SERVICE_SNAPSHOT_REQUIREMENT_FIELDS
        },
    }


def _get_service_execution_snapshot_requirements(*, service_execution):
    """
    Retourne les requirements contractuels d'une ServiceExecution.

    Le snapshot constitue la source de vérité historique de l'exécution.
    Aucun fallback vers le Service courant n'est autorisé : une
    ServiceExecution sans snapshot valide est considérée comme invalide.
    """
    snapshot = service_execution.service_snapshot_json

    if not isinstance(snapshot, dict):
        raise ValueError(
            "ServiceExecution sans service_snapshot_json valide."
        )

    requirements = snapshot.get("requirements")

    if not isinstance(requirements, dict):
        raise ValueError(
            "ServiceExecution sans snapshot requirements valide."
        )

    missing_fields = [
        field_name
        for field_name in SERVICE_SNAPSHOT_REQUIREMENT_FIELDS
        if field_name not in requirements
    ]

    if missing_fields:
        raise ValueError(
            "Snapshot ServiceExecution incomplet : requirements manquants : "
            + ", ".join(sorted(missing_fields))
            + "."
        )

    invalid_fields = [
        field_name
        for field_name in SERVICE_SNAPSHOT_REQUIREMENT_FIELDS
        if not isinstance(requirements[field_name], bool)
    ]

    if invalid_fields:
        raise ValueError(
            "Snapshot ServiceExecution invalide : requirements non booléens : "
            + ", ".join(sorted(invalid_fields))
            + "."
        )

    return requirements


@transaction.atomic
def create_service_execution(
    *,
    order,
    service,
    asset=None,
    metadata_json=None,
    notes="",
):
    """
    Porte canonique de création d'une ServiceExecution.

    Garanties :
    - statut initial pending ;
    - moteur d'exécution snapshoté depuis Service.primary_engine ;
    - séquence calculée par commande ;
    - allocation de séquence sérialisée par verrou sur la commande ;
    - configuration du Service snapshotée ;
    - aucun sous-objet métier créé implicitement.
    """
    if order.pk is None:
        raise ValueError(
            "Une commande persistée est requise pour créer "
            "une ServiceExecution."
        )

    if service.pk is None:
        raise ValueError(
            "Un Service persisté est requis pour créer "
            "une ServiceExecution."
        )

    # Le Service est un contrat catalogue : une nouvelle exécution
    # ne peut être créée qu'à partir d'une définition valide.
    #
    # full_clean() reste volontairement ici, à la frontière de création
    # opérationnelle, afin de ne pas modifier globalement le comportement
    # de Service.save() pendant la strangulation du legacy.
    service.full_clean()

    if not service.is_active:
        raise ValueError(
            "Impossible de créer une ServiceExecution "
            "à partir d'un Service inactif."
        )

    # Verrou transactionnel sur l'agrégat commercial.
    # Il sérialise l'allocation des sequence_index pour cette commande.
    locked_order = (
        order.__class__.objects
        .select_for_update()
        .get(pk=order.pk)
    )

    last_execution = (
        ServiceExecution.objects
        .filter(order=locked_order)
        .order_by("-sequence_index", "-id")
        .first()
    )

    next_sequence_index = (
        last_execution.sequence_index + 1
        if last_execution is not None
        else 1
    )

    service_execution = ServiceExecution(
        order=locked_order,
        service=service,
        asset=asset,
        execution_engine=service.primary_engine,
        status=ServiceExecution.STATUS_PENDING,
        sequence_index=next_sequence_index,
        metadata_json=dict(metadata_json or {}),
        service_snapshot_json=_build_service_execution_snapshot(
            service=service,
        ),
        notes=notes or "",
    )

    # save() applique notamment l'invariant asset/customer.
    service_execution.save()

    return service_execution



@transaction.atomic
def materialize_service_executions_for_order(*, order):
    """
    Matérialise les ServiceExecution canoniques correspondant aux
    familles métier résolues pour une Order.

    Garanties :
    - résolution complète du catalogue avant toute création ;
    - une ServiceExecution maximum créée par Service résolu ;
    - réutilisation des exécutions déjà existantes ;
    - ordre du resolver multiservice conservé ;
    - idempotence sous verrou transactionnel sur l'Order ;
    - création exclusivement via create_service_execution().
    """
    if order is None or order.pk is None:
        raise ValueError(
            "Une commande persistée est requise pour matérialiser "
            "ses ServiceExecution."
        )

    from services.resolution import resolve_v2_services_for_order

    # Résoudre d'abord TOUS les Services.
    #
    # Si le catalogue est incomplet ou contient un Service inactif,
    # resolve_v2_services_for_order() échoue avant toute matérialisation.
    resolved_services = resolve_v2_services_for_order(order)

    locked_order = (
        order.__class__.objects
        .select_for_update()
        .get(pk=order.pk)
    )

    resolved_service_ids = tuple(
        service.id
        for service in resolved_services
    )

    existing_executions = (
        ServiceExecution.objects
        .filter(
            order=locked_order,
            service_id__in=resolved_service_ids,
        )
        .order_by(
            "sequence_index",
            "id",
        )
    )

    existing_by_service_id = {}

    for execution in existing_executions:
        if execution.service_id in existing_by_service_id:
            first_execution = existing_by_service_id[
                execution.service_id
            ]

            raise ValueError(
                "Matérialisation ServiceExecution ambiguë : "
                f"Order #{locked_order.pk} possède plusieurs "
                "ServiceExecution pour le même Service "
                f"#{execution.service_id} "
                f"(executions #{first_execution.pk} "
                f"et #{execution.pk})."
            )

        existing_by_service_id[execution.service_id] = execution

    materialized = []

    for service in resolved_services:
        execution = existing_by_service_id.get(service.id)

        if execution is None:
            execution = create_service_execution(
                order=locked_order,
                service=service,
            )

            existing_by_service_id[service.id] = execution

        materialized.append(execution)

    # ---------------------------------------------------------
    # MATERIALISATION DU BRIDGE ORDERITEM -> SERVICEEXECUTION
    # ---------------------------------------------------------
    #
    # Une ligne commerciale doit être rattachée exactement à
    # l'exécution qui porte sa famille métier.
    #
    # Important :
    # - aucun déplacement silencieux d'un lien existant ;
    # - bag rattache volontairement toutes les lignes à pressing_bag ;
    # - item utilise le resolver canonique ligne par ligne.
    from services.resolution import (
        SERVICE_CODE_PRESSING_BAG,
        resolve_v2_service_code_for_order_item,
    )

    executions_by_service_code = {
        execution.service.code: execution
        for execution in materialized
    }

    pricing_mode = str(
        getattr(locked_order, "pricing_mode", None) or "bag"
    ).strip().lower()

    order_items = list(
        locked_order.items
        .select_related("service__category")
        .all()
    )

    for order_item in order_items:
        if pricing_mode == "bag":
            service_code = SERVICE_CODE_PRESSING_BAG
        else:
            service_code = resolve_v2_service_code_for_order_item(
                order_item
            )

            if not service_code:
                raise ValueError(
                    "Impossible de résoudre le Service V2 de "
                    f"OrderItem #{order_item.pk}."
                )

        execution = executions_by_service_code.get(service_code)

        if execution is None:
            raise ValueError(
                "Aucune ServiceExecution matérialisée pour "
                f"OrderItem #{order_item.pk} et le code "
                f"{service_code!r}."
            )

        existing_link = (
            ServiceExecutionItem.objects
            .filter(order_item=order_item)
            .select_related(
                "service_execution__service",
            )
            .first()
        )

        if existing_link is None:
            ServiceExecutionItem.objects.create(
                service_execution=execution,
                order_item=order_item,
            )
            continue

        if existing_link.service_execution_id != execution.id:
            raise ValueError(
                "OrderItem déjà rattaché à une autre "
                "ServiceExecution : "
                f"OrderItem #{order_item.pk}, "
                f"execution actuelle #{existing_link.service_execution_id}, "
                f"execution attendue #{execution.id}."
            )

    return tuple(materialized)



@transaction.atomic
def finalize_commercial_order(*, order):
    """
    Finalise commercialement une Order après matérialisation
    réussie de toutes ses ServiceExecution canoniques.

    Garanties :
    - exige une Order persistée ;
    - verrouille l'Order pendant la finalisation ;
    - matérialise toutes les exécutions avant de sortir du mode draft ;
    - conserve l'idempotence de la matérialisation ;
    - si la résolution ou la matérialisation échoue, is_draft reste inchangé ;
    - aucune logique d'affectation, paiement ou logistique n'est déclenchée ici.
    """
    if order is None or order.pk is None:
        raise ValueError(
            "Une commande persistée est requise pour "
            "la finalisation commerciale."
        )

    locked_order = (
        order.__class__.objects
        .select_for_update()
        .get(pk=order.pk)
    )

    executions = materialize_service_executions_for_order(
        order=locked_order,
    )

    if getattr(locked_order, "is_draft", False):
        locked_order.is_draft = False
        locked_order.save(
            update_fields=[
                "is_draft",
            ]
        )

    return executions


ALLOWED_SERVICE_EXECUTION_TRANSITIONS = {
    ServiceExecution.STATUS_PENDING: {
        ServiceExecution.STATUS_SCHEDULED,
        ServiceExecution.STATUS_CANCELED,
        ServiceExecution.STATUS_FAILED,
    },
    ServiceExecution.STATUS_SCHEDULED: {
        ServiceExecution.STATUS_IN_PROGRESS,
        ServiceExecution.STATUS_CANCELED,
        ServiceExecution.STATUS_FAILED,
    },
    ServiceExecution.STATUS_IN_PROGRESS: {
        ServiceExecution.STATUS_AWAITING_VALIDATION,
        ServiceExecution.STATUS_COMPLETED,
        ServiceExecution.STATUS_CANCELED,
        ServiceExecution.STATUS_FAILED,
    },
    ServiceExecution.STATUS_AWAITING_VALIDATION: {
        ServiceExecution.STATUS_IN_PROGRESS,
        ServiceExecution.STATUS_COMPLETED,
        ServiceExecution.STATUS_CANCELED,
        ServiceExecution.STATUS_FAILED,
    },
    ServiceExecution.STATUS_COMPLETED: set(),
    ServiceExecution.STATUS_CANCELED: set(),
    ServiceExecution.STATUS_FAILED: set(),
}


def _append_note(service_execution, note):
    if not note:
        return

    service_execution.notes = (
        f"{service_execution.notes}\n{note}".strip()
        if service_execution.notes
        else note
    )


def _validate_transition(*, service_execution, target_status):
    current_status = service_execution.status

    allowed_targets = ALLOWED_SERVICE_EXECUTION_TRANSITIONS.get(
        current_status,
        set(),
    )

    if target_status not in allowed_targets:
        raise ValueError(
            "Transition ServiceExecution interdite : "
            f"{current_status} -> {target_status}."
        )


def _save_transition(
    *,
    service_execution,
    target_status,
    note="",
    extra_update_fields=None,
):
    _validate_transition(
        service_execution=service_execution,
        target_status=target_status,
    )

    service_execution.status = target_status
    _append_note(service_execution, note)

    update_fields = {
        "status",
        "notes",
        "updated_at",
    }

    if extra_update_fields:
        update_fields.update(extra_update_fields)

    service_execution.save(
        update_fields=sorted(update_fields),
    )

    return service_execution


@transaction.atomic
def schedule_service_execution(
    *,
    service_execution,
    planned_start_at=None,
    planned_end_at=None,
    note="",
):
    _validate_transition(
        service_execution=service_execution,
        target_status=ServiceExecution.STATUS_SCHEDULED,
    )

    service_execution.status = ServiceExecution.STATUS_SCHEDULED

    if planned_start_at is not None:
        service_execution.planned_start_at = planned_start_at

    if planned_end_at is not None:
        service_execution.planned_end_at = planned_end_at

    _append_note(service_execution, note)

    service_execution.save(
        update_fields=[
            "status",
            "planned_start_at",
            "planned_end_at",
            "notes",
            "updated_at",
        ]
    )

    return service_execution


@transaction.atomic
def start_service_execution(*, service_execution, note=""):
    _validate_transition(
        service_execution=service_execution,
        target_status=ServiceExecution.STATUS_IN_PROGRESS,
    )

    service_execution.status = ServiceExecution.STATUS_IN_PROGRESS

    if service_execution.started_at is None:
        service_execution.started_at = timezone.now()

    _append_note(service_execution, note)

    service_execution.save(
        update_fields=[
            "status",
            "started_at",
            "notes",
            "updated_at",
        ]
    )

    return service_execution


@transaction.atomic
def await_service_execution_validation(
    *,
    service_execution,
    note="",
):
    return _save_transition(
        service_execution=service_execution,
        target_status=ServiceExecution.STATUS_AWAITING_VALIDATION,
        note=note,
    )


@transaction.atomic
def complete_service_execution(*, service_execution, note=""):
    _validate_transition(
        service_execution=service_execution,
        target_status=ServiceExecution.STATUS_COMPLETED,
    )

    service_execution.status = ServiceExecution.STATUS_COMPLETED

    if service_execution.completed_at is None:
        service_execution.completed_at = timezone.now()

    _append_note(service_execution, note)

    service_execution.save(
        update_fields=[
            "status",
            "completed_at",
            "notes",
            "updated_at",
        ]
    )

    return service_execution


@transaction.atomic
def cancel_service_execution(*, service_execution, note=""):
    _validate_transition(
        service_execution=service_execution,
        target_status=ServiceExecution.STATUS_CANCELED,
    )

    service_execution.status = ServiceExecution.STATUS_CANCELED

    if service_execution.canceled_at is None:
        service_execution.canceled_at = timezone.now()

    _append_note(service_execution, note)

    service_execution.save(
        update_fields=[
            "status",
            "canceled_at",
            "notes",
            "updated_at",
        ]
    )

    return service_execution


@transaction.atomic
def fail_service_execution(*, service_execution, note=""):
    return _save_transition(
        service_execution=service_execution,
        target_status=ServiceExecution.STATUS_FAILED,
        note=note,
    )


def evaluate_service_execution_completion(*, service_execution):
    """
    Évalue si une ServiceExecution possède tous les éléments nécessaires
    à sa complétion.

    Cette fonction est volontairement READ-ONLY :
    elle ne modifie aucun statut.

    Retour :
    {
        "ready": bool,
        "missing": [...],
        "checks": {...},
    }
    """
    requirements = _get_service_execution_snapshot_requirements(
        service_execution=service_execution,
    )

    checks = {}
    missing = []

    # ---------------------------------------------------------
    # LOGISTIQUE
    # ---------------------------------------------------------
    if requirements["requires_logistics"]:
        missions = service_execution.missions.all()

        has_missions = missions.exists()
        all_completed = (
            has_missions
            and not missions.exclude(status="completed").exists()
        )

        checks["logistics"] = {
            "required": True,
            "has_objects": has_missions,
            "satisfied": all_completed,
        }

        if not has_missions:
            missing.append("logistics:no_mission")
        elif not all_completed:
            missing.append("logistics:missions_not_completed")
    else:
        checks["logistics"] = {
            "required": False,
            "has_objects": None,
            "satisfied": True,
        }

    # ---------------------------------------------------------
    # PARTENAIRE
    # ---------------------------------------------------------
    if requirements["requires_partner"]:
        partner_jobs = service_execution.partner_jobs.all()

        has_partner_jobs = partner_jobs.exists()
        all_handed_over = (
            has_partner_jobs
            and not partner_jobs.exclude(status="handed_over").exists()
        )

        checks["partner"] = {
            "required": True,
            "has_objects": has_partner_jobs,
            "satisfied": all_handed_over,
        }

        if not has_partner_jobs:
            missing.append("partner:no_partner_job")
        elif not all_handed_over:
            missing.append("partner:jobs_not_handed_over")
    else:
        checks["partner"] = {
            "required": False,
            "has_objects": None,
            "satisfied": True,
        }

    # ---------------------------------------------------------
    # PESEE
    # ---------------------------------------------------------
    if requirements["requires_weighing"]:
        has_final_weighing = service_execution.weighing_records.filter(
            weighing_stage="final_validation",
        ).exists()

        checks["weighing"] = {
            "required": True,
            "has_objects": has_final_weighing,
            "satisfied": has_final_weighing,
        }

        if not has_final_weighing:
            missing.append("weighing:no_final_validation")
    else:
        checks["weighing"] = {
            "required": False,
            "has_objects": None,
            "satisfied": True,
        }

    # ---------------------------------------------------------
    # OTP
    # Relations accessibles via Mission -> otp_records
    # ---------------------------------------------------------
    if requirements["requires_otp"]:
        has_approved_otp = service_execution.missions.filter(
            otp_records__status="approved",
        ).exists()

        checks["otp"] = {
            "required": True,
            "has_objects": has_approved_otp,
            "satisfied": has_approved_otp,
        }

        if not has_approved_otp:
            missing.append("otp:no_approved_otp")
    else:
        checks["otp"] = {
            "required": False,
            "has_objects": None,
            "satisfied": True,
        }

    # ---------------------------------------------------------
    # SIGNATURE
    # Relations accessibles via Mission -> signatures
    # ---------------------------------------------------------
    if requirements["requires_signature"]:
        has_validated_signature = service_execution.missions.filter(
            signatures__status="validated",
        ).exists()

        checks["signature"] = {
            "required": True,
            "has_objects": has_validated_signature,
            "satisfied": has_validated_signature,
        }

        if not has_validated_signature:
            missing.append("signature:no_validated_signature")
    else:
        checks["signature"] = {
            "required": False,
            "has_objects": None,
            "satisfied": True,
        }

    # ---------------------------------------------------------
    # DEVIS
    # ---------------------------------------------------------
    if requirements["requires_quote"]:
        has_final_quote = service_execution.price_quotes.filter(
            quote_type="final",
            is_final=True,
        ).exists()

        checks["quote"] = {
            "required": True,
            "has_objects": has_final_quote,
            "satisfied": has_final_quote,
        }

        if not has_final_quote:
            missing.append("quote:no_final_quote")
    else:
        checks["quote"] = {
            "required": False,
            "has_objects": None,
            "satisfied": True,
        }

    # ---------------------------------------------------------
    # RENDEZ-VOUS
    # ---------------------------------------------------------
    if requirements["requires_appointment"]:
        has_started_appointment = service_execution.started_at is not None

        checks["appointment"] = {
            "required": True,
            "has_objects": has_started_appointment,
            "satisfied": has_started_appointment,
        }

        if not has_started_appointment:
            missing.append("appointment:not_started")
    else:
        checks["appointment"] = {
            "required": False,
            "has_objects": None,
            "satisfied": True,
        }

    # ---------------------------------------------------------
    # ACTIF / EQUIPEMENT CLIENT
    # ---------------------------------------------------------
    if requirements["requires_asset"]:
        has_asset = service_execution.asset_id is not None

        checks["asset"] = {
            "required": True,
            "has_objects": has_asset,
            "satisfied": has_asset,
        }

        if not has_asset:
            missing.append("asset:no_asset")
    else:
        checks["asset"] = {
            "required": False,
            "has_objects": None,
            "satisfied": True,
        }

    # ---------------------------------------------------------
    # CAPACITES NON ENCORE MODELISABLES PAR SERVICEEXECUTION
    # ---------------------------------------------------------
    unresolved_capabilities = []

    checks["unresolved_capabilities"] = unresolved_capabilities

    for capability in unresolved_capabilities:
        missing.append(f"unresolved:{capability}")

    return {
        "ready": not missing,
        "missing": missing,
        "checks": checks,
    }


@transaction.atomic
def complete_service_execution_if_ready(
    *,
    service_execution,
    note="",
):
    """
    Réconcilie une ServiceExecution avec ses prérequis de complétion.

    Cette fonction constitue l'unique porte automatique de clôture.

    Principes :
    - réutilise evaluate_service_execution_completion() ;
    - ne démarre jamais implicitement une exécution ;
    - ne force jamais une transition invalide ;
    - est idempotente pour les statuts terminaux ;
    - complète uniquement depuis in_progress ou awaiting_validation.

    Retour :
    {
        "completed": bool,
        "status": str,
        "ready": bool,
        "missing": [...],
        "checks": {...},
        "reason": str,
    }
    """

    terminal_statuses = {
        ServiceExecution.STATUS_COMPLETED,
        ServiceExecution.STATUS_CANCELED,
        ServiceExecution.STATUS_FAILED,
    }

    if service_execution.status in terminal_statuses:
        evaluation = evaluate_service_execution_completion(
            service_execution=service_execution,
        )

        return {
            "completed": (
                service_execution.status
                == ServiceExecution.STATUS_COMPLETED
            ),
            "status": service_execution.status,
            "ready": evaluation["ready"],
            "missing": evaluation["missing"],
            "checks": evaluation["checks"],
            "reason": "terminal_status",
        }

    evaluation = evaluate_service_execution_completion(
        service_execution=service_execution,
    )

    if not evaluation["ready"]:
        return {
            "completed": False,
            "status": service_execution.status,
            "ready": False,
            "missing": evaluation["missing"],
            "checks": evaluation["checks"],
            "reason": "requirements_not_satisfied",
        }

    completable_statuses = {
        ServiceExecution.STATUS_IN_PROGRESS,
        ServiceExecution.STATUS_AWAITING_VALIDATION,
    }

    if service_execution.status not in completable_statuses:
        return {
            "completed": False,
            "status": service_execution.status,
            "ready": True,
            "missing": [],
            "checks": evaluation["checks"],
            "reason": "status_not_completable",
        }

    complete_service_execution(
        service_execution=service_execution,
        note=note or (
            "ServiceExecution complétée automatiquement "
            "après validation des prérequis métier."
        ),
    )

    return {
        "completed": True,
        "status": service_execution.status,
        "ready": True,
        "missing": [],
        "checks": evaluation["checks"],
        "reason": "completed",
    }
