from django.contrib import admin
from django.contrib.admin import ModelAdmin as UnfoldModelAdmin

from .models import (
    CustomerAsset,
    Service,
    ServiceCategory,
    ServiceExecution,
    ServiceOption,
)


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


@admin.register(CustomerAsset)
class CustomerAssetAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "customer",
        "asset_type",
        "name",
        "reference",
        "is_active",
        "created_at",
    )

    search_fields = (
        "customer__name",
        "customer__phone",
        "name",
        "reference",
        "description",
    )

    list_filter = (
        "asset_type",
        "is_active",
        "created_at",
    )

    autocomplete_fields = (
        "customer",
    )

    ordering = (
        "customer",
        "name",
    )


@admin.register(ServiceExecution)
class ServiceExecutionAdmin(UnfoldModelAdmin):
    def has_add_permission(self, request):
        """
        Les ServiceExecution doivent être créées exclusivement via
        services.services.create_service_execution().
        """
        return False

    list_display = (
        "id",
        "order",
        "service",
        "asset",
        "execution_engine",
        "status",
        "sequence_index",
        "planned_start_at",
        "started_at",
        "completed_at",
        "created_at",
    )

    search_fields = (
        "order__code",
        "service__code",
        "service__name",
        "notes",
    )

    list_filter = (
        "execution_engine",
        "status",
        "service",
        "created_at",
    )

    autocomplete_fields = (
        "order",
        "service",
        "asset",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    ordering = (
        "-created_at",
    )

    fieldsets = (
        (
            "Référence",
            {
                "fields": (
                    "order",
                    "service",
                    "asset",
                    "sequence_index",
                )
            },
        ),
        (
            "Exécution",
            {
                "fields": (
                    "execution_engine",
                    "status",
                )
            },
        ),
        (
            "Planification",
            {
                "fields": (
                    "planned_start_at",
                    "planned_end_at",
                    "started_at",
                    "completed_at",
                    "canceled_at",
                )
            },
        ),
        (
            "Données métier",
            {
                "fields": (
                    "metadata_json",
                    "service_snapshot_json",
                    "notes",
                )
            },
        ),
        (
            "Audit",
            {
                "fields": (
                    "created_at",
                    "updated_at",
                )
            },
        ),
    )
