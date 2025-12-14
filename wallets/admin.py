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
        Logique métier :
        - Si status passe à 'paid' (et qu'on n'a pas encore débité ce retrait),
          alors on débite le wallet du livreur et on crée une WalletTransaction.
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

        # Si ce n'est PAS un passage vers "paid", on ne fait rien
        if obj.status != "paid":
            return

        # Si déjà traité : vérifier s'il y a déjà une WalletTransaction correspondante
        existing_tx = WalletTransaction.objects.filter(
            wallet=obj.wallet,
            amount=obj.amount,
            type="payout",
            direction="out",
            description__icontains=f"Retrait {obj.id}",
        ).exists()

        if existing_tx:
            return  # On ne recrée pas un deuxième débit

        wallet = obj.wallet

        # Sécurité : vérifier solde suffisant
        if wallet.balance < obj.amount:
            messages.error(
                request,
                f"Solde insuffisant sur le wallet (solde actuel {wallet.balance} XOF) "
                f"pour payer ce retrait de {obj.amount} XOF."
            )
            return

        # Débit effectif du wallet + création de la transaction
        with transaction.atomic():
            # 1) Débit du wallet
            amount_dec = obj.amount
            if not isinstance(amount_dec, Decimal):
                amount_dec = Decimal(str(amount_dec))
            amount_dec = amount_dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            wallet.balance = (wallet.balance - amount_dec).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            wallet.save(update_fields=["balance", "updated_at"])

            # 2) Transaction wallet
            WalletTransaction.objects.create(
                wallet=wallet,
                order=None,
                type="payout",
                direction="out",
                amount=amount_dec,
                description=f"Retrait {obj.id} livreur {wallet.delivery_partner or wallet}",
            )

        messages.success(
            request,
            f"Retrait #{obj.id} payé et wallet livreur débité de {obj.amount} XOF."
        )
