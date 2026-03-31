from django.contrib import admin
from .models import TrackingEvent, Proof, Incident


@admin.register(TrackingEvent)
class TrackingEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "event_type",
        "actor_user",
        "actor_role",
        "status_before",
        "status_after",
        "created_at",
    )
    search_fields = (
        "order__id",
        "title",
        "description",
        "event_type",
        "actor_user__username",
        "actor_user__email",
    )
    list_filter = ("event_type", "actor_role", "created_at")
    ordering = ("-created_at",)
    autocomplete_fields = ("order", "mission", "partner_job", "actor_user")


@admin.register(Proof)
class ProofAdmin(admin.ModelAdmin):
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
class IncidentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
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
        "title",
        "description",
        "incident_type",
        "reported_by__username",
        "assigned_to__username",
    )
    list_filter = ("incident_type", "status", "severity", "reported_at", "created_at")
    ordering = ("-reported_at",)
    autocomplete_fields = ("order", "mission", "partner_job", "reported_by", "assigned_to")
