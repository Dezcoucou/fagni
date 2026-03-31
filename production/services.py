from django.db import transaction
from django.utils import timezone

from production.models import PartnerJob, WeighingRecord


def _generate_partner_job_code(order_id: int) -> str:
    ts = timezone.now().strftime("%Y%m%d%H%M%S")
    return f"JOB-{order_id}-{ts}"


@transaction.atomic
def create_partner_job(*, order, partner, notes=""):
    job = PartnerJob.objects.create(
        code=_generate_partner_job_code(order.id),
        order=order,
        partner=partner,
        status="awaiting_reception",
        notes=notes or "",
    )
    return job


@transaction.atomic
def mark_partner_job_received(*, partner_job, notes=""):
    if partner_job.status not in {"awaiting_reception", "issue"}:
        raise ValueError(f"Impossible de réceptionner un job au statut {partner_job.status}")

    partner_job.status = "received"
    partner_job.received_at = timezone.now()
    if notes:
        partner_job.notes = (partner_job.notes + "\n" + notes).strip() if partner_job.notes else notes

    partner_job.save(update_fields=["status", "received_at", "notes", "updated_at"])
    return partner_job


@transaction.atomic
def mark_partner_job_processing(*, partner_job, notes=""):
    if partner_job.status not in {"received", "weighed", "confirmed"}:
        raise ValueError(f"Impossible de lancer le traitement au statut {partner_job.status}")

    partner_job.status = "processing"
    partner_job.processing_started_at = timezone.now()
    if notes:
        partner_job.notes = (partner_job.notes + "\n" + notes).strip() if partner_job.notes else notes

    partner_job.save(update_fields=["status", "processing_started_at", "notes", "updated_at"])
    return partner_job


@transaction.atomic
def mark_partner_job_ready(*, partner_job, notes=""):
    if partner_job.status not in {"processing", "confirmed", "weighed"}:
        raise ValueError(f"Impossible de passer prêt au statut {partner_job.status}")

    partner_job.status = "ready"
    partner_job.ready_at = timezone.now()
    if notes:
        partner_job.notes = (partner_job.notes + "\n" + notes).strip() if partner_job.notes else notes

    partner_job.save(update_fields=["status", "ready_at", "notes", "updated_at"])
    return partner_job


@transaction.atomic
def handover_partner_job(*, partner_job, notes=""):
    if partner_job.status != "ready":
        raise ValueError(f"Impossible de remettre au livreur un job au statut {partner_job.status}")

    partner_job.status = "handed_over"
    partner_job.handed_over_at = timezone.now()
    if notes:
        partner_job.notes = (partner_job.notes + "\n" + notes).strip() if partner_job.notes else notes

    partner_job.save(update_fields=["status", "handed_over_at", "notes", "updated_at"])
    return partner_job


@transaction.atomic
def record_weighing(
    *,
    order,
    net_weight,
    weighing_stage,
    partner_job=None,
    mission=None,
    gross_weight=None,
    performed_by_role="driver",
    unit="kg",
    notes="",
):
    record = WeighingRecord.objects.create(
        order=order,
        partner_job=partner_job,
        mission=mission,
        performed_by_role=performed_by_role,
        weighing_stage=weighing_stage,
        gross_weight=gross_weight,
        net_weight=net_weight,
        unit=unit,
        notes=notes or "",
    )

    if partner_job and partner_job.status in {"awaiting_reception", "received"}:
        partner_job.status = "weighed"
        partner_job.save(update_fields=["status", "updated_at"])

    return record
