from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from .models import PricingRule, PriceQuote


@admin.register(PricingRule)
class PricingRuleAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "service",
        "partner",
        "rule_type",
        "label",
        "value",
        "is_active",
        "priority",
        "starts_at",
        "ends_at",
        "created_at",
    )
    search_fields = ("service__name", "partner__name", "label")
    list_filter = ("rule_type", "is_active", "service", "partner", "created_at")
    ordering = ("service", "priority", "label")
    autocomplete_fields = ("service", "partner")


@admin.register(PriceQuote)
class PriceQuoteAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "order",
        "quote_type",
        "subtotal_amount",
        "logistics_fee",
        "service_fee",
        "discount_amount",
        "total_amount",
        "currency",
        "is_final",
        "generated_at",
    )
    search_fields = ("order__id", "notes")
    list_filter = ("quote_type", "is_final", "currency", "generated_at")
    ordering = ("-generated_at",)
    autocomplete_fields = ("order",)
