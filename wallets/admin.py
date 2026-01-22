from decimal import Decimal, ROUND_HALF_UP

from django.contrib import admin, messages
from django.db import transaction

from .models import Wallet, WalletTransaction, WithdrawalRequest


# =========================
#  WALLET
# =========================
@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "owner_type",
        "customer",
        "laundry_partner",
        "delivery_partner",
        "balance",
        "currency",
        "created_at",
    )
    list_filter = ("owner_type", "currency")
    search_fields = (
        "customer__name",
        "customer__phone",
        "laundry_partner__name",
        "delivery_partner__name",
    )
    readonly_fields = ("created_at", "updated_at")


# =========================
#  WALLET TRANSACTIONS
# =========================
@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "wallet",
        "type",
        "direction",
        "amount",
        "created_at",
        "order",
    )
    list_filter = ("type", "direction", "created_at")
    search_fields = (
        "wallet__customer__name",
        "wallet__laundry_partner__name",
        "wallet__delivery_partner__name",
        "order__code",
        "description",
    )
    readonly_fields = ("created_at",)


# =========================
#  WITHDRAWAL REQUESTS
# =========================
@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    """
    Demandes de retrait de wallets livreurs.
    Le modèle WithdrawalRequest n'a PAS de champ `driver` ni `updated_at`,
    donc on dérive le livreur via wallet.delivery_partner.
    """

    list_display = ("id", "wallet", "get_driver", "amount", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = (
        "wallet__delivery_partner__name",
        "wallet__delivery_partner__email",
    )
    # On ne met que des champs qui existent vraiment sur le modèle
    readonly_fields = ("wallet", "created_at")

    ordering = ("-created_at",)

    def get_driver(self, obj):
        """
        Affiche le nom du livreur lié au wallet (si présent).
        """
        if obj.wallet and obj.wallet.delivery_partner:
            return obj.wallet.delivery_partner.name
        return "—"

    get_driver.short_description = "Livreur"

    def save_model(self, request, obj, form, change):
        """
        Source unique : le modèle (obj.apply_payout) gère TOUT :
        - lock wallet
        - anti-doublon
        - création de WalletTransaction
        - processed_at
        L'admin ne fait que déclencher au bon moment.
        """
        # Ancien statut avant sauvegarde
        old_status = None
        if obj.pk:
            old = WithdrawalRequest.objects.filter(pk=obj.pk).only("status").first()
            if old:
                old_status = old.status

        super().save_model(request, obj, form, change)

        # On recharge en base après super().save_model()
        obj.refresh_from_db()

        # Ne déclencher que lors d'un passage vers "paid"
        if obj.status != "paid":
            return
        if old_status == "paid":
            return

        try:
            obj.apply_payout()
            obj.refresh_from_db()
            if obj.processed_at:
                messages.success(
                    request,
                    f"Retrait #{obj.id} payé : wallet débité de {obj.amount} XOF."
                )
            else:
                messages.warning(
                    request,
                    f"Retrait #{obj.id} : paiement non appliqué (déjà traité, tx existante ou solde insuffisant)."
                )
        except Exception as e:
            messages.error(request, f"Erreur paiement retrait #{obj.id} : {e}")
