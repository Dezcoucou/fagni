from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from .models import (
    ReferralLink,
    ReferralCommission,
    WalletTransaction,
    WithdrawalRequest,
    MLMSettings,
)


@admin.register(ReferralLink)
class ReferralLinkAdmin(UnfoldModelAdmin):
    list_display = (
        "referral_code",
        "customer",
        "actor_type",
        "sponsor",
        "created_at",
    )
    search_fields = (
        "referral_code",
        "customer__name",
        "customer__phone",
    )
    autocomplete_fields = ("customer", "sponsor")
    list_filter = ("actor_type",)


@admin.register(ReferralCommission)
class ReferralCommissionAdmin(UnfoldModelAdmin):
    list_display = (
        "beneficiary_profile",
        "level",
        "order",
        "service_fee_base",
        "commission_percent",
        "commission_amount",
        "created_at",
    )
    list_filter = ("level",)
    search_fields = (
        "beneficiary_profile__referral_code",
        "beneficiary_profile__customer__name",
        "order__code",
    )
    autocomplete_fields = ("beneficiary_profile", "order")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(UnfoldModelAdmin):
    list_display = (
        "profile",
        "type",
        "amount",
        "order",
        "created_at",
    )
    list_filter = ("type",)
    search_fields = (
        "profile__referral_code",
        "profile__customer__name",
        "order__code",
    )
    autocomplete_fields = ("profile", "order")


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(UnfoldModelAdmin):
    list_display = (
        "profile",
        "amount",
        "status",
        "payout_method",
        "created_at",
        "processed_at",
        "processed_by",
    )
    list_filter = ("status", "payout_method")
    search_fields = (
        "profile__referral_code",
        "profile__customer__name",
    )
    autocomplete_fields = ("profile", "processed_by")


@admin.register(MLMSettings)
class MLMSettingsAdmin(UnfoldModelAdmin):
    list_display = (
        "name",
        "is_active",
        "n1_percent",
        "n2_percent",
        "n3_percent",
        "total_percent",
        "min_service_fee_for_commission",
        "min_withdrawal_amount",
        "max_daily_withdrawal",
        "allow_negative_wallet",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")
