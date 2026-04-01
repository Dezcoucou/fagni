from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from .models import UserRole, CustomerProfile, DriverProfile


@admin.register(UserRole)
class UserRoleAdmin(UnfoldModelAdmin):
    list_display = ("id", "user", "role", "is_primary", "created_at")
    search_fields = ("user__username", "user__email", "role")
    list_filter = ("role", "is_primary", "created_at")
    ordering = ("-created_at",)


@admin.register(CustomerProfile)
class CustomerProfileAdmin(UnfoldModelAdmin):
    list_display = ("id", "user", "default_address", "preferred_contact_mode", "created_at")
    search_fields = ("user__username", "user__email", "user__first_name", "user__last_name")
    list_filter = ("preferred_contact_mode", "created_at")
    ordering = ("-created_at",)


@admin.register(DriverProfile)
class DriverProfileAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "user",
        "display_name",
        "phone_number",
        "vehicle_type",
        "zone",
        "is_available",
        "is_verified",
        "rating",
        "created_at",
    )
    search_fields = (
        "user__username",
        "user__email",
        "display_name",
        "phone_number",
        "vehicle_plate",
        "zone",
    )
    list_filter = ("vehicle_type", "is_available", "is_verified", "zone", "created_at")
    ordering = ("-created_at",)
