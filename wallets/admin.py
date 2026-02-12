from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone

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
        "leg",
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
    Demandes de retrait.
    Objectif: payer / rejeter proprement + tracer processed_by + anti-doublon.
    """

    list_display = (
        "id",
        "wallet",
        "get_beneficiary",
        "amount",
        "status",
        "created_at",
        "processed_at",
        "processed_by",
    )
    list_filter = ("status", "created_at", "processed_at")
    search_fields = (
        "wallet__delivery_partner__name",
        "wallet__delivery_partner__email",
        "admin_notes",
        "wallet__laundry_partner__name",
        "wallet__customer__name",
    )
    ordering = ("-created_at",)

    # On évite de modifier wallet & montant après création (sécurité)
    readonly_fields = ("wallet", "requested_by", "amount", "created_at", "processed_at", "processed_by")

    fieldsets = (
        ("Demande", {
            "fields": ("wallet", "requested_by", "amount", "status", "created_at")
        }),
        ("Traitement", {
            "fields": ("processed_at", "processed_by", "admin_notes")
        }),
    )

    actions = ("action_mark_approved", "action_mark_paid", "action_mark_rejected")

    def get_beneficiary(self, obj):
        return obj.get_beneficiary_display() if obj else "—"

    get_beneficiary.short_description = "Bénéficiaire"

    # ---------- ACTIONS ADMIN (LISTE) ----------

    @admin.action(description="✅ Approuver (status=approved)")
    def action_mark_approved(self, request, queryset):
        n = 0
        for obj in queryset.select_related("wallet", "wallet__delivery_partner", "wallet__laundry_partner", "wallet__customer"):
            if obj.status in ("paid", "rejected"):
                continue
            obj.status = "approved"
            obj.processed_by = request.user
            # On garde processed_at vide pour approved (optionnel) — tu peux le mettre si tu veux tracer
            obj.save(update_fields=["status", "processed_by"])
            n += 1
        if n:
            messages.success(request, f"{n} demande(s) approuvée(s).")
        else:
            messages.info(request, "Aucune demande approuvée (déjà payée/rejetée ?).")

    @admin.action(description="💸 Payer (status=paid + débit wallet + tx payout OUT)")
    def action_mark_paid(self, request, queryset):
        ok = 0
        skipped = 0
        errors = 0

        for obj in queryset.select_related("wallet", "wallet__delivery_partner", "wallet__laundry_partner", "wallet__customer"):
            try:
                if obj.status == "paid" or obj.processed_at is not None:
                    skipped += 1
                    continue

                # Sécurité: si tx existe déjà, on ne repaye pas
                if obj._tx_exists():
                    skipped += 1
                    continue

                # Passage à paid + trace du user
                obj.status = "paid"
                obj.processed_by = request.user
                obj.save(update_fields=["status", "processed_by"])

                # Applique payout (idempotent + atomic + lock wallet)
                obj.apply_payout()
                obj.refresh_from_db()

                if obj.processed_at:
                    ok += 1
                else:
                    # cas solde insuffisant / tx déjà existante / déjà traité
                    skipped += 1
            except Exception as e:
                errors += 1
                messages.error(request, f"Erreur paiement retrait #{obj.id} : {e}")

        if ok:
            messages.success(request, f"{ok} retrait(s) payé(s) avec succès.")
        if skipped:
            messages.warning(request, f"{skipped} retrait(s) ignoré(s) (déjà traité/tx existante/solde insuffisant).")
        if not ok and not errors and skipped == 0:
            messages.info(request, "Aucun retrait traité.")

    @admin.action(description="❌ Rejeter (status=rejected)")
    def action_mark_rejected(self, request, queryset):
        n = 0
        for obj in queryset.select_related("wallet", "wallet__delivery_partner", "wallet__laundry_partner", "wallet__customer"):
            if obj.status == "paid":
                continue
            obj.status = "rejected"
            obj.processed_by = request.user
            # ici on met processed_at pour tracer que c'est “traité” (mais pas payé)
            obj.processed_at = timezone.now()
            obj.save(update_fields=["status", "processed_by", "processed_at"])
            n += 1

        if n:
            messages.success(request, f"{n} demande(s) rejetée(s).")
        else:
            messages.info(request, "Aucune demande rejetée (déjà payée ?).")

    # ---------- EDIT (FORM) : si on change status à la main ----------

    def save_model(self, request, obj, form, change):
        """
        Si on édite une demande et qu'on met status=paid:
        - on trace processed_by
        - on applique payout (idempotent)
        """
        old_status = None
        if obj.pk:
            old = WithdrawalRequest.objects.filter(pk=obj.pk).only("status", "processed_at").first()
            if old:
                old_status = old.status

        # Trace l'admin qui traite (sur changement de statut)
        if change and "status" in form.changed_data:
            obj.processed_by = request.user

            # Pour rejected: processed_at = now (trace)
            if obj.status == "rejected" and not obj.processed_at:
                obj.processed_at = timezone.now()

        super().save_model(request, obj, form, change)

        # Déclenchement payout uniquement sur transition vers paid
        obj.refresh_from_db()
        if obj.status == "paid" and old_status != "paid":
            try:
                obj.apply_payout()
                obj.refresh_from_db()
                if obj.processed_at:
                    messages.success(request, f"Retrait #{obj.id} payé : wallet débité de {obj.amount} XOF.")
                else:
                    messages.warning(request, f"Retrait #{obj.id} : paiement non appliqué (déjà traité/tx existante/solde insuffisant).")
            except Exception as e:
                messages.error(request, f"Erreur paiement retrait #{obj.id} : {e}")
