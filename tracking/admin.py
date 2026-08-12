from django.contrib import admin
from django.contrib.admin import ModelAdmin as UnfoldModelAdmin
from .models import TrackingEvent, Proof, Incident


@admin.register(TrackingEvent)
class TrackingEventAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "order",
        "service_execution",
        "event_type",
        "actor_user",
        "actor_role",
        "status_before",
        "status_after",
        "created_at",
    )
    search_fields = (
        "order__id",
        "service_execution__service__code",
        "service_execution__service__name",
        "title",
        "description",
        "event_type",
        "actor_user__username",
        "actor_user__email",
    )
    list_filter = (
        "event_type",
        "actor_role",
        "service_execution__execution_engine",
        "created_at",
    )
    ordering = ("-created_at",)
    autocomplete_fields = (
        "order",
        "service_execution",
        "mission",
        "partner_job",
        "actor_user",
    )


@admin.register(Proof)
class ProofAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "order",
        "proof_type",
        "captured_by",
        "captured_at",
        "created_at",
    )
    search_fields = (
        "order__id",
        "text_value",
        "notes",
        "captured_by__username",
        "captured_by__email",
    )
    list_filter = ("proof_type", "captured_at", "created_at")
    ordering = ("-captured_at",)
    autocomplete_fields = ("order", "mission", "partner_job", "captured_by")


@admin.register(Incident)
class IncidentAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "order",
        "service_execution",
        "incident_type",
        "status",
        "severity",
        "reported_by",
        "assigned_to",
        "reported_at",
        "resolved_at",
        "created_at",
    )
    search_fields = (
        "order__id",
        "service_execution__service__code",
        "service_execution__service__name",
        "title",
        "description",
        "incident_type",
        "reported_by__username",
        "assigned_to__username",
    )
    list_filter = (
        "incident_type",
        "status",
        "severity",
        "service_execution__execution_engine",
        "reported_at",
        "created_at",
    )
    ordering = ("-reported_at",)
    autocomplete_fields = (
        "order",
        "service_execution",
        "mission",
        "partner_job",
        "reported_by",
        "assigned_to",
    )
