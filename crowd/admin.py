from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from .models import Cotransporter, CotransporterRoute, CrowdSettings


@admin.register(Cotransporter)
class CotransporterAdmin(UnfoldModelAdmin):
    list_display = ("id", "user", "phone", "is_active", "is_verified", "score", "total_trips", "successful_trips")
    list_filter = ("is_active", "is_verified", "vehicle_type")
    search_fields = ("user__username", "phone")
    readonly_fields = ("created_at", "updated_at", "total_trips", "successful_trips", "score")


@admin.register(CotransporterRoute)
class CotransporterRouteAdmin(UnfoldModelAdmin):
    list_display = ("id", "cotransporter", "origin_label", "destination_label", "departure_time", "is_active")
    list_filter = ("is_active", "cotransporter")
    search_fields = ("origin_label", "destination_label", "cotransporter__user__username")
    readonly_fields = ("created_at", "updated_at")


@admin.register(CrowdSettings)
class CrowdSettingsAdmin(UnfoldModelAdmin):
    list_display = ("id",)
