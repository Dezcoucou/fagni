from django.db import transaction
from django.utils import timezone

from services.models import ServiceExecution


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
