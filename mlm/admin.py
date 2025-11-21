from django.contrib import admin
from .models import ReferralLink, ReferralCommission, WalletTransaction

# Personnalisation globale de l'admin Django
admin.site.site_header = "FAGNI – Administration"
admin.site.site_title = "FAGNI – Backoffice"
admin.site.index_title = "Tableau de bord FAGNI"


@admin.register(ReferralLink)
class ReferralLinkAdmin(admin.ModelAdmin):
    list_display = ("referral_code", "actor_type", "sponsor", "created_at")
    list_filter = ("actor_type",)
    search_fields = ("referral_code",)


@admin.register(ReferralCommission)
class ReferralCommissionAdmin(admin.ModelAdmin):
    list_display = (
        "beneficiary_profile",
        "level",
        "order",
        "service_fee_base",
        "commission_percent",
        "commission_amount",
        "is_paid",
        "created_at",
    )
    list_filter = ("level", "is_paid")
    search_fields = ("order__code", "beneficiary_profile__referral_code")


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("profile", "type", "amount", "order", "created_at")
    list_filter = ("type",)
    search_fields = ("profile__referral_code", "order__code")
