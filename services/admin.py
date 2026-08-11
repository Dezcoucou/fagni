from django.contrib import admin
from django.contrib.admin import ModelAdmin as UnfoldModelAdmin

from .models import Service, ServiceCategory, ServiceOption


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "code",
        "name",
        "is_active",
        "created_at",
    )

    search_fields = (
        "code",
        "name",
        "description",
    )

    list_filter = (
        "is_active",
        "created_at",
    )

    prepopulated_fields = {
        "code": ("name",),
    }

    ordering = ("name",)


@admin.register(Service)
class ServiceAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "code",
        "name",
        "category",
        "primary_engine",
        "pricing_mode",
        "requires_partner",
        "requires_logistics",
        "requires_appointment",
        "requires_quote",
        "requires_asset",
        "is_active",
        "default_sla_hours",
    )

    search_fields = (
        "code",
        "name",
        "description",
    )

    list_filter = (
        "category",
        "primary_engine",
        "pricing_mode",
        "requires_partner",
        "requires_logistics",
        "requires_weighing",
        "requires_appointment",
        "requires_quote",
        "requires_asset",
        "requires_otp",
        "requires_signature",
        "is_active",
    )

    prepopulated_fields = {
        "code": ("name",),
    }

    ordering = ("category", "name")

    fieldsets = (
        (
            "Identité",
            {
                "fields": (
                    "code",
                    "category",
                    "name",
                    "description",
                    "is_active",
                )
            },
        ),
        (
            "Exécution",
            {
                "fields": (
                    "primary_engine",
                    "requires_partner",
                    "requires_logistics",
                    "requires_weighing",
                    "requires_appointment",
                    "requires_quote",
                    "requires_asset",
                    "requires_otp",
                    "requires_signature",
                )
            },
        ),
        (
            "Tarification & SLA",
            {
                "fields": (
                    "pricing_mode",
                    "default_sla_hours",
                )
            },
        ),
    )


@admin.register(ServiceOption)
class ServiceOptionAdmin(UnfoldModelAdmin):
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

    search_fields = (
        "service__name",
        "code",
        "name",
        "description",
    )

    list_filter = (
        "service",
        "extra_price_type",
        "is_active",
        "created_at",
    )

    prepopulated_fields = {
        "code": ("name",),
    }

    ordering = (
        "service",
        "name",
    )
