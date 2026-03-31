from django.contrib import admin
from .models import Payment, Payout


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "payment_reference",
        "order",
        "payment_method",
        "status",
        "amount",
        "currency",
        "provider_name",
        "paid_at",
        "created_at",
    )
    search_fields = (
        "payment_reference",
        "provider_transaction_id",
        "provider_name",
        "order__id",
    )
    list_filter = ("payment_method", "status", "currency", "paid_at", "created_at")
    ordering = ("-created_at",)
    autocomplete_fields = ("order",)


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "beneficiary_type",
        "beneficiary_partner",
        "beneficiary_driver",
        "amount",
        "currency",
        "status",
        "scheduled_at",
        "paid_at",
        "created_at",
    )
    search_fields = ("order__id", "reference", "beneficiary_partner__name", "beneficiary_driver__name")
    list_filter = ("beneficiary_type", "status", "currency", "created_at")
    ordering = ("-created_at",)
    autocomplete_fields = ("order", "beneficiary_partner", "beneficiary_driver")
