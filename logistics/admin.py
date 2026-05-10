from django.contrib import admin
from django.contrib.admin import ModelAdmin as UnfoldModelAdmin
from .models import Mission, MissionActionLog


@admin.register(Mission)
class MissionAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "code",
        "order",
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
        "contact_name",
        "contact_phone",
        "instructions",
    )
    list_filter = (
        "mission_type",
        "status",
        "priority",
        "planned_start_at",
        "created_at",
    )
    ordering = ("-created_at",)
    autocomplete_fields = ("order", "source_address", "destination_address")


@admin.register(MissionActionLog)
class MissionActionLogAdmin(UnfoldModelAdmin):
    list_display = ("id", "mission", "action_type", "performed_at", "created_at")
    search_fields = ("mission__code", "action_type", "notes")
    list_filter = ("action_type", "performed_at", "created_at")
    ordering = ("-created_at",)
    autocomplete_fields = ("mission",)
