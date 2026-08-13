from django.db import transaction
from django.utils import timezone

from services.models import ServiceExecution



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
    service = service_execution.service

    checks = {}
    missing = []

    # ---------------------------------------------------------
    # LOGISTIQUE
    # ---------------------------------------------------------
    if service.requires_logistics:
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
    if service.requires_partner:
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
    if service.requires_weighing:
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
    if service.requires_otp:
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
    if service.requires_signature:
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
    if service.requires_quote:
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
    if service.requires_appointment:
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
    if service.requires_asset:
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
