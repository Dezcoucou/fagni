from django.db import transaction
from django.utils import timezone

from logistics.models import Mission, MissionActionLog


def _generate_mission_code(order_id: int, mission_type: str) -> str:
    ts = timezone.now().strftime("%Y%m%d%H%M%S")
    short_type = mission_type[:3].upper()
    return f"MSN-{order_id}-{short_type}-{ts}"


def _validate_service_execution_order(*, order, service_execution):
    """
    Garantit qu'une Mission ne peut pas être attachée à une
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
            "Impossible de créer une mission : commande non persistée."
        )

    if execution_order_id != order_id:
        raise ValueError(
            "ServiceExecution incompatible : "
            "l'exécution de service et la mission doivent appartenir "
            "à la même commande."
        )


@transaction.atomic
def create_mission_for_order(
    *,
    order,
    mission_type,
    service_execution=None,
    source_address=None,
    destination_address=None,
    contact_name="",
    contact_phone="",
    priority="normal",
    instructions="",
    planned_start_at=None,
    planned_end_at=None,
    sequence_index=1,
):
    """
    Crée une Mission logistique.

    Compatibilité :
    - legacy : order seul reste accepté ;
    - multiservices : service_execution peut être fourni.

    Aucune ServiceExecution n'est créée implicitement ici.
    """

    _validate_service_execution_order(
        order=order,
        service_execution=service_execution,
    )

    mission = Mission.objects.create(
        code=_generate_mission_code(
            order.id,
            mission_type,
        ),
        order=order,
        service_execution=service_execution,
        mission_type=mission_type,
        status="assigned",
        source_address=source_address,
        destination_address=destination_address,
        contact_name=contact_name or "",
        contact_phone=contact_phone or "",
        priority=priority,
        instructions=instructions or "",
        planned_start_at=planned_start_at,
        planned_end_at=planned_end_at,
        sequence_index=sequence_index,
    )

    return mission


@transaction.atomic
def start_mission(*, mission, notes=""):
    if mission.status not in {"assigned", "accepted"}:
        raise ValueError(
            f"Impossible de démarrer une mission au statut {mission.status}"
        )

    mission.status = "en_route"
    mission.started_at = timezone.now()

    mission.save(
        update_fields=[
            "status",
            "started_at",
            "updated_at",
        ]
    )

    MissionActionLog.objects.create(
        mission=mission,
        action_type="started",
        notes=notes or "Mission démarrée",
    )

    return mission


@transaction.atomic
def mark_mission_arrived(*, mission, notes=""):
    if mission.status not in {"en_route", "accepted"}:
        raise ValueError(
            f"Impossible de marquer arrivée une mission au statut {mission.status}"
        )

    mission.status = "arrived"
    mission.arrived_at = timezone.now()

    mission.save(
        update_fields=[
            "status",
            "arrived_at",
            "updated_at",
        ]
    )

    MissionActionLog.objects.create(
        mission=mission,
        action_type="arrived",
        notes=notes or "Arrivée confirmée",
    )

    return mission


@transaction.atomic
def accept_mission(*, mission, notes=""):
    if mission.status != "assigned":
        raise ValueError(
            f"Impossible d'accepter une mission au statut {mission.status}"
        )

    mission.status = "accepted"

    mission.save(
        update_fields=[
            "status",
            "updated_at",
        ]
    )

    MissionActionLog.objects.create(
        mission=mission,
        action_type="started",
        notes=notes or "Mission acceptée",
    )

    return mission


@transaction.atomic
def complete_mission(
    *,
    mission,
    notes="",
    action_type="completed",
):
    if mission.status in {"completed", "failed", "canceled"}:
        raise ValueError(
            "La mission est déjà finalisée avec le statut "
            f"{mission.status}"
        )

    mission.status = "completed"
    mission.completed_at = timezone.now()

    mission.save(
        update_fields=[
            "status",
            "completed_at",
            "updated_at",
        ]
    )

    MissionActionLog.objects.create(
        mission=mission,
        action_type=action_type,
        notes=notes or "Mission terminée",
    )

    if mission.service_execution_id is not None:
        from services.services import complete_service_execution_if_ready

        complete_service_execution_if_ready(
            service_execution=mission.service_execution,
            note=(
                "ServiceExecution réévaluée après complétion "
                f"de la mission {mission.code}."
            ),
        )

    return mission


@transaction.atomic
def cancel_mission(
    *,
    mission,
    reason="",
    notes="",
):
    """
    Annule une Mission logistique FAGNI.

    Contrat :
    - completed : annulation interdite ;
    - failed : annulation interdite ;
    - canceled : opération idempotente ;
    - autres états : annulables ;
    - reason obligatoire lors de la première annulation ;
    - canceled_at horodaté ;
    - MissionActionLog conserve le motif structuré.
    """

    if mission.status == "canceled":
        return mission

    if mission.status in {"completed", "failed"}:
        raise ValueError(
            "Impossible d'annuler une mission déjà finalisée "
            f"avec le statut {mission.status}"
        )

    reason = str(reason or "").strip()

    if not reason:
        raise ValueError(
            "Le motif d'annulation (reason) est obligatoire "
            "pour garantir l'auditabilité."
        )

    mission.status = "canceled"

    if mission.canceled_at is None:
        mission.canceled_at = timezone.now()

    mission.save(
        update_fields=[
            "status",
            "canceled_at",
            "updated_at",
        ]
    )

    log_notes = f"Motif : {reason}"

    if notes:
        log_notes = f"{log_notes}\n{notes}"

    MissionActionLog.objects.create(
        mission=mission,
        action_type="canceled",
        notes=log_notes,
        payload_json={
            "reason": reason,
        },
    )

    return mission
