from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import render

from logistics.models import Mission
from logistics.models_pdf_log import MissionPdfSendLog


STATUS_LABELS = {
    "assigned": "Assignée",
    "accepted": "Acceptée",
    "en_route": "En route",
    "arrived": "Arrivée",
    "in_progress": "En cours",
    "awaiting_validation": "En attente de validation",
    "completed": "Terminée",
    "failed": "Échouée",
}


def logistics_dashboard(request):
    mission_qs = Mission.objects.all().order_by("-created_at")

    q = (request.GET.get("q", "") or "").strip()
    status = (request.GET.get("status", "") or "").strip()
    date_from = (request.GET.get("date_from", "") or "").strip()
    date_to = (request.GET.get("date_to", "") or "").strip()

    if q:
        mission_qs = mission_qs.filter(code__icontains=q)

    if status:
        mission_qs = mission_qs.filter(status=status)

    if date_from:
        mission_qs = mission_qs.filter(created_at__date__gte=date_from)

    if date_to:
        mission_qs = mission_qs.filter(created_at__date__lte=date_to)

    paginator = Paginator(mission_qs, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    stats = {
        "total": Mission.objects.count(),
        "completed": Mission.objects.filter(status="completed").count(),
        "in_progress": Mission.objects.filter(
            status__in=["assigned", "accepted", "en_route", "arrived", "in_progress", "awaiting_validation"]
        ).count(),
        "failed": Mission.objects.filter(status="failed").count(),
        "pdf_sent": MissionPdfSendLog.objects.filter(status="sent").count(),
        "pdf_failed": MissionPdfSendLog.objects.filter(status="failed").count(),
    }

    status_breakdown_raw = (
        Mission.objects.values("status")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    status_breakdown = [
        {
            "status": item["status"],
            "label": STATUS_LABELS.get(item["status"], item["status"]),
            "total": item["total"],
        }
        for item in status_breakdown_raw
    ]

    mission_status_choices = [
        ("assigned", "Assignée"),
        ("accepted", "Acceptée"),
        ("en_route", "En route"),
        ("arrived", "Arrivée"),
        ("in_progress", "En cours"),
        ("awaiting_validation", "En attente de validation"),
        ("completed", "Terminée"),
        ("failed", "Échouée"),
    ]

    recent_pdf_logs = MissionPdfSendLog.objects.select_related("mission").order_by("-created_at")[:10]

    context = {
        "missions": page_obj.object_list,
        "page_obj": page_obj,
        "stats": stats,
        "status_breakdown": status_breakdown,
        "mission_status_choices": mission_status_choices,
        "recent_pdf_logs": recent_pdf_logs,
        "status_labels": STATUS_LABELS,
        "filters": {
            "q": q,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
        },
    }
    return render(request, "logistics/dashboard.html", context)
