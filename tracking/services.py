from django.db import transaction

from tracking.models import TrackingEvent, Incident, Proof


def _validate_service_execution_contract(
    *,
    order,
    service_execution,
    mission=None,
    partner_job=None,
):
    """
    Garantit la cohérence :
    Order <-> ServiceExecution <-> Mission / PartnerJob.

    service_execution=None reste autorisé pendant le strangler legacy.
    """
    if service_execution is None:
        return

    order_id = getattr(order, "id", None)
    execution_order_id = getattr(service_execution, "order_id", None)

    if not order_id:
        raise ValueError(
            "Impossible de rattacher un événement tracking : "
            "commande non persistée."
        )

    if execution_order_id != order_id:
        raise ValueError(
            "ServiceExecution incompatible : "
            "l'exécution de service et l'objet tracking doivent "
            "appartenir à la même commande."
        )

    if mission is not None:
        if mission.order_id != order_id:
            raise ValueError(
                "Mission incompatible : "
                "la Mission et l'objet tracking doivent appartenir "
                "à la même commande."
            )

        if (
            mission.service_execution_id is not None
            and mission.service_execution_id != service_execution.id
        ):
            raise ValueError(
                "Mission incompatible : "
                "la Mission appartient à une autre ServiceExecution."
            )

    if partner_job is not None:
        if partner_job.order_id != order_id:
            raise ValueError(
                "PartnerJob incompatible : "
                "le PartnerJob et l'objet tracking doivent appartenir "
                "à la même commande."
            )

        if (
            partner_job.service_execution_id is not None
            and partner_job.service_execution_id != service_execution.id
        ):
            raise ValueError(
                "PartnerJob incompatible : "
                "le PartnerJob appartient à une autre ServiceExecution."
            )


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
    service_execution=None,
    status_before="",
    status_after="",
    metadata_json=None,
):
    _validate_service_execution_contract(
        order=order,
        service_execution=service_execution,
        mission=mission,
        partner_job=partner_job,
    )

    event = TrackingEvent.objects.create(
        order=order,
        service_execution=service_execution,
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
    service_execution=None,
):
    _validate_service_execution_contract(
        order=order,
        service_execution=service_execution,
        mission=mission,
        partner_job=partner_job,
    )

    incident = Incident.objects.create(
        order=order,
        service_execution=service_execution,
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
