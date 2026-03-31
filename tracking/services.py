from django.db import transaction

from tracking.models import TrackingEvent, Incident, Proof


@transaction.atomic
def create_tracking_event(
    *,
    order,
    event_type,
    title,
    description="",
    actor_user=None,
    actor_role="system",
    mission=None,
    partner_job=None,
    status_before="",
    status_after="",
    metadata_json=None,
):
    event = TrackingEvent.objects.create(
        order=order,
        mission=mission,
        partner_job=partner_job,
        event_type=event_type,
        actor_user=actor_user,
        actor_role=actor_role,
        status_before=status_before or "",
        status_after=status_after or "",
        title=title,
        description=description or "",
        metadata_json=metadata_json or {},
    )
    return event


@transaction.atomic
def create_incident(
    *,
    order,
    incident_type,
    title,
    description,
    severity="medium",
    reported_by=None,
    assigned_to=None,
    mission=None,
    partner_job=None,
):
    incident = Incident.objects.create(
        order=order,
        mission=mission,
        partner_job=partner_job,
        incident_type=incident_type,
        status="open",
        severity=severity,
        reported_by=reported_by,
        assigned_to=assigned_to,
        title=title,
        description=description,
    )
    return incident


@transaction.atomic
def attach_proof(
    *,
    order,
    proof_type,
    mission=None,
    partner_job=None,
    captured_by=None,
    file=None,
    text_value="",
    notes="",
):
    proof = Proof.objects.create(
        order=order,
        mission=mission,
        partner_job=partner_job,
        proof_type=proof_type,
        file=file,
        text_value=text_value or "",
        captured_by=captured_by,
        notes=notes or "",
    )
    return proof
