from django.contrib import admin
from .models import ServiceCategory, Service, ServiceOption


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "code", "name", "is_active", "created_at")
    search_fields = ("code", "name", "description")
    list_filter = ("is_active", "created_at")
    prepopulated_fields = {"code": ("name",)}
    ordering = ("name",)


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "code",
        "name",
        "category",
        "pricing_mode",
        "requires_partner",
        "requires_logistics",
        "requires_weighing",
        "is_active",
        "default_sla_hours",
        "created_at",
    )
    search_fields = ("code", "name", "description")
    list_filter = (
        "category",
        "pricing_mode",
        "requires_partner",
        "requires_logistics",
        "requires_weighing",
        "is_active",
        "created_at",
    )
    prepopulated_fields = {"code": ("name",)}
    ordering = ("name",)


@admin.register(ServiceOption)
class ServiceOptionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "service",
        "code",
        "name",
        "extra_price_type",
        "extra_price_value",
        "is_active",
        "created_at",
    )
    search_fields = ("service__name", "code", "name", "description")
    list_filter = ("service", "extra_price_type", "is_active", "created_at")
    prepopulated_fields = {"code": ("name",)}
    ordering = ("service", "name")
