from django.db import transaction
from django.utils import timezone

from logistics.models import Mission, MissionActionLog


def _generate_mission_code(order_id: int, mission_type: str) -> str:
    ts = timezone.now().strftime("%Y%m%d%H%M%S")
    short_type = mission_type[:3].upper()
    return f"MSN-{order_id}-{short_type}-{ts}"


@transaction.atomic
def create_mission_for_order(
    *,
    order,
    mission_type,
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
    mission = Mission.objects.create(
        code=_generate_mission_code(order.id, mission_type),
        order=order,
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
        raise ValueError(f"Impossible de démarrer une mission au statut {mission.status}")

    mission.status = "en_route"
    mission.started_at = timezone.now()
    mission.save(update_fields=["status", "started_at", "updated_at"])

    MissionActionLog.objects.create(
        mission=mission,
        action_type="started",
        notes=notes or "Mission démarrée",
    )
    return mission


@transaction.atomic
def mark_mission_arrived(*, mission, notes=""):
    if mission.status not in {"en_route", "accepted"}:
        raise ValueError(f"Impossible de marquer arrivée une mission au statut {mission.status}")

    mission.status = "arrived"
    mission.arrived_at = timezone.now()
    mission.save(update_fields=["status", "arrived_at", "updated_at"])

    MissionActionLog.objects.create(
        mission=mission,
        action_type="arrived",
        notes=notes or "Arrivée confirmée",
    )
    return mission


@transaction.atomic
def accept_mission(*, mission, notes=""):
    if mission.status != "assigned":
        raise ValueError(f"Impossible d'accepter une mission au statut {mission.status}")

    mission.status = "accepted"
    mission.save(update_fields=["status", "updated_at"])

    MissionActionLog.objects.create(
        mission=mission,
        action_type="started",
        notes=notes or "Mission acceptée",
    )
    return mission


@transaction.atomic
def complete_mission(*, mission, notes="", action_type="completed"):
    if mission.status in {"completed", "failed"}:
        raise ValueError(f"La mission est déjà finalisée avec le statut {mission.status}")

    mission.status = "completed"
    mission.completed_at = timezone.now()
    mission.save(update_fields=["status", "completed_at", "updated_at"])

    MissionActionLog.objects.create(
        mission=mission,
        action_type=action_type,
        notes=notes or "Mission terminée",
    )
    return mission
