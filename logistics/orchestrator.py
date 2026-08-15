from decimal import Decimal

from django.db import transaction

from logistics.adapters import (
    get_order_contact_name,
    get_order_contact_phone,
    get_order_destination_address_v2,
    get_order_source_address_v2,
    get_or_create_smoke_partner,
)
from logistics.services import (
    complete_mission,
    create_mission_for_order,
    start_mission,
)
from pricing.services import create_estimated_quote
from services.models import ServiceExecution
from services.services import (
    schedule_service_execution,
    start_service_execution,
)
from production.services import (
    create_partner_job,
    mark_partner_job_ready,
    mark_partner_job_received,
    record_weighing,
)
from tracking.services import (
    create_incident,
    create_tracking_event,
)


@transaction.atomic
def create_pickup_flow_from_order(
    order,
    service_execution=None,
):
    """
    Crée et exécute un flux minimal de collecte pour une commande.

    service_execution reste optionnel pendant la migration legacy.
    """

    source_address = get_order_source_address_v2(order)
    destination_address = get_order_destination_address_v2(order)

    if source_address is None or destination_address is None:
        raise ValueError(
            "Impossible de résoudre des adresses V2 compatibles "
            "pour cette commande."
        )

    mission = create_mission_for_order(
        order=order,
        service_execution=service_execution,
        mission_type="pickup_from_customer",
        source_address=source_address,
        destination_address=destination_address,
        contact_name=get_order_contact_name(order),
        contact_phone=get_order_contact_phone(order),
        priority="normal",
        instructions="Mission de collecte créée par orchestrateur V2",
        sequence_index=1,
    )

    mission = start_mission(
        mission=mission,
        notes="Mission démarrée par orchestrateur V2",
    )

    mission = complete_mission(
        mission=mission,
        notes="Mission terminée par orchestrateur V2",
        action_type="completed",
    )

    metadata = {
        "flow": "create_pickup_flow_from_order",
        "mission_code": mission.code,
    }

    if service_execution is not None:
        metadata["service_execution_id"] = service_execution.id

    create_tracking_event(
        order=order,
        service_execution=service_execution,
        mission=mission,
        event_type="pickup_confirmed",
        actor_role="system",
        title="Collecte V2 terminée",
        description="Mission de collecte exécutée via orchestrateur V2",
        status_before="assigned",
        status_after="completed",
        metadata_json=metadata,
    )

    return mission


@transaction.atomic
def create_partner_processing_flow(
    order,
    partner=None,
    mission=None,
    service_execution=None,
):
    """
    Crée et exécute un flux minimal de traitement partenaire.

    Compatibilité :
    - legacy : service_execution peut rester None ;
    - multiservices : le PartnerJob est rattaché à la ServiceExecution.

    L'invariant d'appartenance à la même Order est vérifié
    par production.services.create_partner_job().
    """

    if partner is None:
        partner = get_or_create_smoke_partner()

    partner_job = create_partner_job(
        order=order,
        partner=partner,
        service_execution=service_execution,
        notes="PartnerJob créé par orchestrateur V2",
    )

    partner_job = mark_partner_job_received(
        partner_job=partner_job,
        notes="Réception partenaire confirmée par orchestrateur V2",
    )

    weighing_record = record_weighing(
        order=order,
        partner_job=partner_job,
        mission=mission,
        service_execution=service_execution,
        net_weight=Decimal("3.50"),
        gross_weight=Decimal("3.80"),
        weighing_stage="partner_reception",
        performed_by_role="partner",
        unit="kg",
        notes="Pesée créée par orchestrateur V2",
    )

    partner_job = mark_partner_job_ready(
        partner_job=partner_job,
        notes="Commande prête côté partenaire via orchestrateur V2",
    )

    create_tracking_event(
        order=order,
        service_execution=service_execution,
        mission=mission,
        partner_job=partner_job,
        event_type="production_ready",
        actor_role="system",
        title="Traitement partenaire prêt",
        description="Flux partenaire exécuté via orchestrateur V2",
        status_before="received",
        status_after="ready",
        metadata_json={
            "flow": "create_partner_processing_flow",
            "partner_job_code": partner_job.code,
            "service_execution_id": (
                service_execution.id
                if service_execution is not None
                else None
            ),
            "weight": str(weighing_record.net_weight),
            "unit": weighing_record.unit,
        },
    )

    return partner_job, weighing_record


@transaction.atomic
def run_minimal_v2_flow(
    order,
    create_incident_flag=False,
    service_execution=None,
):
    """
    Exécute le flux minimal V2.

    Le paramètre service_execution est optionnel afin de conserver
    la compatibilité avec les actions admin et commandes V2 historiques.
    """

    if service_execution is not None:
        if service_execution.status == ServiceExecution.STATUS_PENDING:
            service_execution = schedule_service_execution(
                service_execution=service_execution,
                note="ServiceExecution planifiée par orchestrateur V2",
            )

        if service_execution.status == ServiceExecution.STATUS_SCHEDULED:
            service_execution = start_service_execution(
                service_execution=service_execution,
                note="ServiceExecution démarrée par orchestrateur V2",
            )

    mission = create_pickup_flow_from_order(
        order,
        service_execution=service_execution,
    )

    partner_job, weighing_record = create_partner_processing_flow(
        order=order,
        partner=None,
        mission=mission,
        service_execution=service_execution,
    )

    quote = create_estimated_quote(
        order=order,
        service_execution=service_execution,
        notes="Devis estimatif généré par orchestrateur V2",
    )

    incident = None

    if create_incident_flag:
        incident = create_incident(
            order=order,
            service_execution=service_execution,
            mission=mission,
            partner_job=partner_job,
            incident_type="delay",
            title="Incident test orchestrateur V2",
            description="Incident généré par run_minimal_v2_flow",
            severity="low",
        )

    return {
        "order": order,
        "service_execution": service_execution,
        "mission": mission,
        "partner_job": partner_job,
        "weighing_record": weighing_record,
        "quote": quote,
        "incident": incident,
    }
