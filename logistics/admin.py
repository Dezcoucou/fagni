from django.contrib import admin
from django.contrib.admin import ModelAdmin as UnfoldModelAdmin

from .models import Mission, MissionActionLog


@admin.register(Mission)
class MissionAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "code",
        "order",
        "service_execution",
        "mission_type",
        "status",
        "contact_name",
        "contact_phone",
        "priority",
        "planned_start_at",
        "completed_at",
        "created_at",
    )

    search_fields = (
        "code",
        "order__id",
        "order__customer_name",
        "service_execution__service__code",
        "service_execution__service__name",
        "contact_name",
        "contact_phone",
        "instructions",
    )

    list_filter = (
        "mission_type",
        "status",
        "priority",
        "service_execution__execution_engine",
        "planned_start_at",
        "created_at",
    )

    ordering = (
        "-created_at",
    )

    autocomplete_fields = (
        "order",
        "service_execution",
        "source_address",
        "destination_address",
    )


@admin.register(MissionActionLog)
class MissionActionLogAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "mission",
        "action_type",
        "performed_at",
        "created_at",
    )

    search_fields = (
        "mission__code",
        "action_type",
        "notes",
    )

    list_filter = (
        "action_type",
        "performed_at",
        "created_at",
    )

    ordering = (
        "-created_at",
    )

    autocomplete_fields = (
        "mission",
    )
