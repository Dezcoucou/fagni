from decimal import Decimal
from django.core.exceptions import ValidationError
from django.contrib import admin, messages
from django.utils.html import format_html
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from django.db import transaction

from orders.service_layer.payouts import trigger_driver_payout_for_leg

from .models import (
    Customer,
    Order,
    OrderItem,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
    DeliveryLeg,
    LogisticsConfig,
)

from .config_models import (
    GlobalPricingSettings,
    WorkflowSettings,
    AssignmentSettings,
    InvoiceSettings,
)


# ============================================================
#  LOGISTICS CONFIG
# ============================================================

@admin.register(LogisticsConfig)
class LogisticsConfigAdmin(admin.ModelAdmin):
    list_display = ("id",)


# ============================================================
#  INLINES
# ============================================================

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    fields = ("service", "designation", "quantity", "unit_price", "total")
    readonly_fields = ("total",)


# ============================================================
#  CUSTOM FILTERS
# ============================================================

class PaymentBucketFilter(admin.SimpleListFilter):
    title = "Paiement (bucket)"
    parameter_name = "pay_bucket"

    def lookups(self, request, model_admin):
        return (
            ("paid", "Payées"),
            ("partial", "Partiellement payées"),
            ("unpaid", "Impayées"),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if not val:
            return queryset

        if val == "paid":
            return queryset.filter(payment_status="paid")

        if val == "unpaid":
            return queryset.filter(payment_status="unpaid")

        if val == "partial":
            return queryset.exclude(payment_status="paid").filter(amount_paid__gt=0)

        return queryset


class InvoiceBucketFilter(admin.SimpleListFilter):
    """
    Facture (OK/KO) :
    OK = invoice_number + invoice_date + invoice_status == 'paid'
    KO = il manque au moins un élément
    """
    title = "Facture (bucket)"
    parameter_name = "invoice_bucket"

    def lookups(self, request, model_admin):
        return (
            ("ok", "Facture OK"),
            ("ko", "Facture KO"),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if not val:
            return queryset

        ok_qs = queryset.filter(
            invoice_number__isnull=False
        ).exclude(invoice_number="").filter(
            invoice_date__isnull=False,
            invoice_status="paid",
        )

        if val == "ok":
            return ok_qs

        if val == "ko":
            return queryset.exclude(pk__in=ok_qs.values_list("pk", flat=True))

        return queryset


class WalletsDistributedFilter(admin.SimpleListFilter):
    title = "Wallets distribués"
    parameter_name = "wallets_bucket"

    def lookups(self, request, model_admin):
        return (
            ("yes", "Oui"),
            ("no", "Non"),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if not val:
            return queryset
        if val == "yes":
            return queryset.filter(wallets_distributed=True)
        if val == "no":
            return queryset.filter(wallets_distributed=False)
        return queryset


# ============================================================
#  CLIENTS
# ============================================================

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "address", "nb_commandes", "montant_total")
    search_fields = ("name", "phone", "address")
    list_per_page = 50

    def nb_commandes(self, obj):
        return obj.orders.count()

    nb_commandes.short_description = "Nb commandes"

    def montant_total(self, obj):
        agg = obj.orders.aggregate(total=Sum("total"))
        return agg["total"] or 0

    montant_total.short_description = "Montant total (FCFA)"


# ============================================================
#  COMMANDES
# ============================================================

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):

    list_display = (
        "code",
        "customer",
        "status",
        "payment_badge",
        "invoice_badge",
        "total_client_ttc_display",
        "amount_paid",
        "amount_due_display",
        "invoice_display",
        "wallets_distributed_display",
        "laundry_partner",
        "delivery_partner",
        "created_at",
        "front_detail_link",
        "customer_wallet_link",
    )

    list_filter = (
        PaymentBucketFilter,
        "payment_status",
        "status",
        "payment_method",
        InvoiceBucketFilter,
        WalletsDistributedFilter,
        "laundry_partner",
        "delivery_partner",
        "relay_partner",
        "created_at",
    )

    search_fields = (
        "code",
        "invoice_number",
        "payment_reference",
        "customer__name",
        "customer__phone",
    )

    date_hierarchy = "created_at"

    list_select_related = (
        "customer",
        "laundry_partner",
        "delivery_partner",
        "relay_partner",
    )

    inlines = [OrderItemInline]

    actions = [
        "admin_validate_paid_psp",
        "admin_normalize_invoices_paid",
        "admin_recompute_financials",
        "admin_catchup_wallets_paid",
        "admin_catchup_driver_payout_legs",
    ]

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        if obj:
            from django.apps import apps
            WalletTransaction = apps.get_model("wallets", "WalletTransaction")
            if WalletTransaction.objects.filter(order_id=obj.pk).exists():
                if "payment_status" not in ro:
                    ro.append("payment_status")
        return ro

    def save_model(self, request, obj, form, change):
        try:
            super().save_model(request, obj, form, change)
        except ValidationError as e:
            # Message admin propre (pas de traceback)
            self.message_user(request, "❌ " + "; ".join(e.messages), level=messages.ERROR)

    # ============================================================
    #  ACTIONS
    # ============================================================

    @admin.action(description="✅ Valider paiement PSP")
    def admin_validate_paid_psp(self, request, queryset):
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():

                # 🔒 Déjà payé → on ignore
                if order.payment_status == "paid":
                    skipped += 1
                    continue

                # ❌ PSP sans référence → refus
                if not order.payment_reference:
                    skipped += 1
                    continue

                # 🔁 Recalcul financier AVANT paiement
                try:
                    order.update_financials(save=False)
                except Exception:
                    pass

                paid_at = timezone.now()

                # sécurité : on garde méthode / date / statut facture
                order.payment_method = "psp"
                order.payment_date = paid_at

                if not getattr(order, "invoice_date", None):
                    order.invoice_date = paid_at

                if not getattr(order, "invoice_status", None) or order.invoice_status in ("draft", "issued"):
                    order.invoice_status = "paid"

                order.save(update_fields=["payment_method", "payment_date", "invoice_date", "invoice_status"])

                # ✅ Marquer payé via PSP
                order.mark_paid(
                    method="psp",
                    reference=order.payment_reference,
                    paid_at=paid_at,
                    save=True,
                )
                updated += 1

        messages.success(
            request,
            f"Paiement PSP validé : {updated} commande(s). Ignorées : {skipped}."
        )

    @admin.action(description="🧾 Normaliser facture sur commandes payées")
    def admin_normalize_invoices_paid(self, request, queryset):
        """
        Normalise uniquement les champs facture/paiement (sans toucher aux wallets).
        - si payment_status != paid -> skip
        - payment_date : si vide -> invoice_date / updated_at / now
        - invoice_date : si vide -> payment_date
        - invoice_status : si vide/draft/issued -> 'paid'
        """
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():
                if order.payment_status != "paid":
                    skipped += 1
                    continue

                changed_fields = []

                paid_at = order.payment_date or order.invoice_date or order.updated_at or timezone.now()

                if order.payment_date is None:
                    order.payment_date = paid_at
                    changed_fields.append("payment_date")

                if getattr(order, "invoice_date", None) is None:
                    order.invoice_date = paid_at
                    changed_fields.append("invoice_date")

                inv_status = getattr(order, "invoice_status", None)
                if not inv_status or inv_status in ("draft", "issued"):
                    order.invoice_status = "paid"
                    changed_fields.append("invoice_status")

                # invoice_number : si jamais absent, on laisse update_financials le générer
                if not getattr(order, "invoice_number", None):
                    try:
                        order.update_financials(save=False)
                        changed_fields.append("invoice_number")
                    except Exception:
                        pass

                if changed_fields:
                    order.save(update_fields=list(set(changed_fields)))
                    updated += 1
                else:
                    skipped += 1

        messages.success(
            request,
            f"Normalisation factures : {updated} modifiée(s). Ignorées : {skipped}."
        )

    @admin.action(description="🧾 Rattraper distribution wallets (paid)")
    def admin_catchup_wallets_paid(self, request, queryset):
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():
                if order.payment_status != "paid":
                    skipped += 1
                    continue
                if getattr(order, "wallets_distributed", False):
                    skipped += 1
                    continue

                try:
                    order.mark_as_paid_and_distribute()
                    updated += 1
                except Exception:
                    skipped += 1

        messages.success(
            request,
            f"Rattrapage wallets : {updated} OK. Ignorées/erreurs : {skipped}."
        )

    @admin.action(description="🧮 Recalculer montants financiers")
    def admin_recompute_financials(self, request, queryset):
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():
                try:
                    order.update_financials(save=True)
                    updated += 1
                except Exception:
                    skipped += 1

        messages.success(
            request,
            f"Recalcul : {updated} OK. Erreurs : {skipped}."
        )




    @admin.action(description="🚚 Rattraper payout livreur (legs done)")
    def admin_catchup_driver_payout_legs(self, request, queryset):
        updated = 0
        skipped = 0

        with transaction.atomic():
            for order in queryset.select_for_update():
                if order.payment_status != "paid":
                    skipped += 1
                    continue

                try:
                    legs_done = DeliveryLeg.objects.filter(order=order, status="done").select_related("driver")
                    for leg in legs_done:
                        trigger_driver_payout_for_leg(leg)
                    updated += 1
                except Exception:
                    skipped += 1

        messages.success(
            request,
            f"Rattrapage payout legs : {updated} OK. Ignorées/erreurs : {skipped}."
        )

    # ============================================================
    #  READONLY / FORM
    # ============================================================

    readonly_fields = (
        "created_at",
        "updated_at",

        # timeline
        "pickup_time",
        "dropoff_time",
        "wash_complete_time",
        "return_time",
        "delivered_time",

        # montants
        "total",
        "service_fee",
        "distance_km",
        "delivery_fee",
        "driver_logistic_cost",
        "logistic_margin",

        # TVA / revenue
        "vat_rate",
        "vat_base",
        "vat_fagni",
        "fagni_revenue_ht",

        # total client
        "total_client_ttc",

        # distribution
        "wallets_distributed",
        "mlm_distributed",

        # blocs custom
        "mlm_info_display",
        "wallet_info_display",
    )

    autocomplete_fields = (
        "customer",
        "laundry_partner",
        "delivery_partner",
        "relay_partner",
    )

    fieldsets = (
        (
            "Informations générales",
            {
                "fields": (
                    "code",
                    "status",
                    "customer",
                    "notes",
                    "created_at",
                    "updated_at",
                )
            },
        ),
        (
            "Adresses & géolocalisation",
            {
                "fields": (
                    "pickup_address",
                    ("pickup_lat", "pickup_lng"),
                    "delivery_address",
                    ("delivery_lat", "delivery_lng"),
                )
            },
        ),
        (
            "Partenaires",
            {
                "fields": (
                    "laundry_partner",
                    "delivery_partner",
                    "relay_partner",
                )
            },
        ),
        (
            "Paiement & facturation client",
            {
                "fields": (
                    "payment_status",
                    "payment_method",
                    "payment_reference",
                    "payment_date",
                    "amount_paid",
                    "invoice_number",
                    "invoice_date",
                    "invoice_status",
                )
            },
        ),
        (
            "Totaux client (calculés)",
            {
                "fields": (
                    "total_client_ttc",
                    "total",
                    "service_fee",
                )
            },
        ),
        (
            "Distribution (wallets / MLM)",
            {
                "fields": (
                    "wallets_distributed",
                    "mlm_distributed",
                    "mlm_info_display",
                    "wallet_info_display",
                )
            },
        ),
    )

    # ============================================================
    #  BADGES / HELPERS LISTE
    # ============================================================

    def payment_badge(self, obj):
        st = (obj.payment_status or "").lower()
        label = obj.get_payment_status_display()

        if st == "paid":
            return format_html("<b style='color:#16a34a'>✅ {}</b>", label)
        if st in ("partial", "partially_paid"):
            return format_html("<b style='color:#f59e0b'>🟠 {}</b>", label)
        return format_html("<b style='color:#dc2626'>❌ {}</b>", label)

    payment_badge.short_description = "Paiement"

    def invoice_badge(self, obj):
        inv_no = getattr(obj, "invoice_number", None)
        inv_date = getattr(obj, "invoice_date", None)
        inv_status = getattr(obj, "invoice_status", None)

        ok = bool(inv_no) and (inv_date is not None) and (inv_status == "paid")
        if ok:
            return format_html("<b style='color:#16a34a'>✅ OK</b>")
        # si pas payé, on affiche neutre
        if (obj.payment_status or "").lower() != "paid":
            return format_html("<span style='color:#6b7280'>—</span>")
        return format_html("<b style='color:#f59e0b'>⚠️ KO</b>")

    invoice_badge.short_description = "Facture OK ?"

    def total_client_ttc_display(self, obj):
        return obj.total_client_ttc or obj.total or Decimal("0")

    total_client_ttc_display.short_description = "Total client TTC"

    def amount_due_display(self, obj):
        total_ttc = obj.total_client_ttc or obj.total or Decimal("0")
        paid = obj.amount_paid or Decimal("0")
        due = total_ttc - paid
        return max(due, Decimal("0"))

    amount_due_display.short_description = "Reste à payer"

    def invoice_display(self, obj):
        if not obj.invoice_number:
            return "-"
        return format_html("<code>{}</code>", obj.invoice_number)

    invoice_display.short_description = "N° Facture"

    def wallets_distributed_display(self, obj):
        return (
            format_html("<b style='color:#16a34a'>✅ Oui</b>")
            if obj.wallets_distributed
            else format_html("<span style='color:#6b7280'>—</span>")
        )

    wallets_distributed_display.short_description = "Wallets distribués"

    def front_detail_link(self, obj):
        try:
            url = reverse("orders:detail", args=[obj.pk])
            return format_html('<a href="{}" target="_blank">🔍 Voir</a>', url)
        except Exception:
            return "-"

    front_detail_link.short_description = "Fiche front"

    def customer_wallet_link(self, obj):
        if not obj.customer:
            return "-"
        try:
            url = reverse("wallets:customer_wallet_detail", args=[obj.customer.pk])
            return format_html('<a href="{}" target="_blank">💰 Wallet</a>', url)
        except Exception:
            return "-"

    customer_wallet_link.short_description = "Wallet client"

    # ============================================================
    #  BLOCS INFOS (MLM / WALLETS) — read-only
    # ============================================================

    def mlm_info_display(self, obj):
        customer = getattr(obj, "customer", None)
        if not customer:
            return "Aucun client associé."

        profiles_manager = getattr(customer, "referral_profiles", None)
        profile = profiles_manager.first() if profiles_manager else None

        if not profile:
            return "Aucun profil de parrainage pour ce client."

        sponsor = getattr(profile, "sponsor", None)
        if sponsor and getattr(sponsor, "customer", None):
            sponsor_txt = f"Parrain : {sponsor.customer.name} ({sponsor.referral_code})"
        else:
            sponsor_txt = "Parrain : aucun parrain enregistré."

        try:
            affiliate_url = reverse("mlm:affiliate_detail", args=[profile.referral_code])
            link = format_html(
                " – <a href='{}' target='_blank'>Voir fiche affilié MLM</a>",
                affiliate_url,
            )
        except Exception:
            link = ""

        return format_html(
            "Code affilié client : {} – {}{}",
            profile.referral_code,
            sponsor_txt,
            link,
        )

    mlm_info_display.short_description = "Programme Parrainage (MLM)"

    def wallet_info_display(self, obj):
        # import local pour éviter cycles
        from wallets.models import Wallet

        customer = getattr(obj, "customer", None)
        if not customer:
            return "Aucun client associé."

        try:
            wallet = Wallet.objects.get(customer=customer, owner_type="customer")
            solde = wallet.balance
        except Wallet.DoesNotExist:
            wallet = None
            solde = Decimal("0.00")

        if wallet:
            try:
                wallet_url = reverse("wallets:customer_wallet_detail", args=[customer.pk])
                link = format_html(" – <a href='{}' target='_blank'>Voir portefeuille client</a>", wallet_url)
            except Exception:
                link = ""
        else:
            link = " – Aucun portefeuille ouvert pour ce client."

        return format_html("Solde portefeuille client : {} FCFA{}", solde, link)

    wallet_info_display.short_description = "Portefeuille client"
