from django.contrib import admin
from django.contrib.admin import ModelAdmin as UnfoldModelAdmin
from .models import PartnerJob, WeighingRecord


@admin.register(PartnerJob)
class PartnerJobAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "code",
        "order",
        "service_execution",
        "partner",
        "status",
        "received_at",
        "processing_started_at",
        "ready_at",
        "handed_over_at",
        "created_at",
    )
    search_fields = (
        "code",
        "order__id",
        "service_execution__service__code",
        "service_execution__service__name",
        "partner__name",
        "notes",
    )
    list_filter = (
        "status",
        "service_execution__execution_engine",
        "partner",
        "created_at",
        "ready_at",
    )
    ordering = ("-created_at",)
    autocomplete_fields = (
        "order",
        "service_execution",
        "partner",
    )


@admin.register(WeighingRecord)
class WeighingRecordAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "order",
        "partner_job",
        "mission",
        "performed_by_role",
        "weighing_stage",
        "gross_weight",
        "net_weight",
        "unit",
        "recorded_at",
    )
    search_fields = ("order__id", "partner_job__code", "mission__code", "notes")
    list_filter = ("performed_by_role", "weighing_stage", "unit", "recorded_at")
    ordering = ("-recorded_at",)
    autocomplete_fields = ("order", "partner_job", "mission")
