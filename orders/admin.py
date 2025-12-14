from decimal import Decimal
from django.contrib import admin, messages
from django.utils.html import format_html
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from .models import (
    Customer,
    Order,
    OrderItem,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
    DeliveryLeg,
    LogisticsConfig
)
from .config_models import (
    GlobalPricingSettings,
    WorkflowSettings,
    AssignmentSettings,
    InvoiceSettings,
)


# ============================================================
#  ACTIONS ADMIN – COMMANDES
# ============================================================

def mark_paid(modeladmin, request, queryset):
    """
    Marquer les commandes comme PAYÉES
    → génère facture
    → déclenche distribution wallets
    """
    updated = 0
    for order in queryset:
        if order.payment_status != "paid":
            order.payment_status = "paid"
            order.payment_date = timezone.now()
            order.update_financials(save=True)
            order.save()
            updated += 1

    messages.success(
        request,
        f"{updated} commande(s) marquée(s) comme payée(s)."
    )


mark_paid.short_description = "💰 Marquer comme PAYÉ"


def mark_unpaid(modeladmin, request, queryset):
    """
    Repasser en IMPAYÉ (usage exceptionnel)
    """
    updated = queryset.update(
        payment_status="unpaid",
        payment_date=None
    )

    messages.warning(
        request,
        f"{updated} commande(s) repassée(s) en impayée."
    )


mark_unpaid.short_description = "↩️ Marquer comme IMPAYÉ"


def recompute_totals(modeladmin, request, queryset):
    """
    Recalculer tous les montants financiers
    """
    for order in queryset:
        order.update_financials(save=True)

    messages.info(
        request,
        f"{queryset.count()} commande(s) recalculée(s)."
    )


recompute_totals.short_description = "🧮 Recalculer les totaux"


@admin.register(LogisticsConfig)
class LogisticsConfigAdmin(admin.ModelAdmin):
    list_display = ("id",)  # simple, sûr, ça marche toujours


# ==============
#  INLINES
# ==============
class OrderItemInline(admin.TabularInline):
    """
    Lignes de commande affichées dans l'admin de la commande.
    """
    model = OrderItem
    extra = 0
    fields = ("service", "designation", "quantity", "unit_price", "total")
    readonly_fields = ("total",)


# ==============
#  CLIENTS
# ==============
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
#  FILTRES CUSTOM PAIEMENT
# ============================================================
class PaymentBucketFilter(admin.SimpleListFilter):
    title = "Paiement (bucket)"
    parameter_name = "pay_bucket"

    def lookups(self, request, model_admin):
        return (
            ("paid", "Payées (OK)"),
            ("partial", "Partiellement payées"),
            ("unpaid", "Impayées"),
        )

    def queryset(self, request, queryset):
        val = self.value()
        if not val:
            return queryset

        # NB: total_client_ttc est supposé stocké sur Order (update_financials le remplit)
        # Si jamais il n'existe pas sur certaines anciennes lignes, on retombe sur total.
        if val == "paid":
            return queryset.filter(payment_status="paid")
        if val == "unpaid":
            return queryset.filter(payment_status="unpaid")
        if val == "partial":
            # "partial" peut être un statut, mais si tu utilises juste amount_paid,
            # on détecte un paiement incomplet.
            # On prend : non paid, amount_paid > 0
            return queryset.exclude(payment_status="paid").filter(amount_paid__gt=0)
        return queryset


# ==============
#  COMMANDES
# ==============
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "customer",
        "status",
        "payment_badge",
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
        "payment_status",
        "amount_paid",
        "payment_date",
        "payment_method",
        "payment_reference",
        "invoice_number",
    )

    list_filter = (
        "payment_status",
        PaymentBucketFilter,
        "status",
        "laundry_partner",
        "delivery_partner",
        "relay_partner",
        "created_at",
        "payment_status",
        "payment_method",
    )

    search_fields = (
        "code",
        "invoice_number",
        "payment_reference",
        "customer__name",
        "customer__phone",
        "payment_reference",
        "invoice_number",
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
        "admin_validate_paid_cash",
        "admin_validate_paid_psp",
        "admin_mark_unpaid",
        "admin_recompute_financials",
    ]

    # Champs read-only (sécurise l’admin)
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
                    "delivery_fee",
                    "distance_km",
                )
            },
        ),
        (
            "Logistique (calculés)",
            {
                "fields": (
                    "driver_logistic_cost",
                    "logistic_margin",
                )
            },
        ),
        (
            "TVA & revenus FAGNI (calculés)",
            {
                "fields": (
                    "vat_rate",
                    "vat_base",
                    "vat_fagni",
                    "fagni_revenue_ht",
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
        (
            "Timeline opérationnelle",
            {
                "fields": (
                    "pickup_time",
                    "dropoff_time",
                    "wash_complete_time",
                    "return_time",
                    "delivered_time",
                )
            },
        ),
    )
    (
        "Paiement & Facturation",
        {
            "fields": (
                "payment_status",
                "amount_paid",
                "payment_date",
                "payment_method",
                "payment_reference",
                "invoice_number",
            )
        },
    ),

    @admin.action(description="✅ Marquer comme PAYÉ (set date + amount = total_client_ttc)")
    def mark_paid(modeladmin, request, queryset):
        updated = 0
        for order in queryset:
            # recalcul au besoin pour avoir total_client_ttc à jour
            try:
                order.compute_totals(save=False)
            except Exception:
                pass

            total_ttc = getattr(order, "total_client_ttc", None) or order.total or Decimal("0")
            order.payment_status = "paid"
            order.amount_paid = total_ttc
            order.payment_date = timezone.now()

            # Sauvegarde -> déclenche save() -> distribution wallets + invoice number si logique active
            order.save()
            updated += 1

        modeladmin.message_user(request, f"{updated} commande(s) marquée(s) comme PAYÉE(s).")

    @admin.action(description="↩️ Marquer comme IMPAYÉ (reset paiement)")
    def mark_unpaid(modeladmin, request, queryset):
        updated = 0
        for order in queryset:
            order.payment_status = "unpaid"
            order.amount_paid = Decimal("0")
            order.payment_date = None
            order.payment_reference = None
            order.payment_method = None
            # ⚠️ Option : on ne touche pas invoice_number (traçabilité)
            order.save()
            updated += 1

        modeladmin.message_user(request, f"{updated} commande(s) marquée(s) IMPAYÉE(s).")

    @admin.action(description="🧮 Recalculer les totaux (finance) pour les commandes sélectionnées")
    def recompute_totals(modeladmin, request, queryset):
        updated = 0
        for order in queryset:
            try:
                order.compute_totals(save=True)
                updated += 1
            except Exception:
                continue
        modeladmin.message_user(request, f"Totaux recalculés pour {updated} commande(s).")

    # ============================================================
    #  COLONNES / BADGES LISTE
    # ============================================================
    def payment_badge(self, obj):
        """
        Badge compact sur la liste admin.
        """
        st = (obj.payment_status or "").lower()
        label = obj.get_payment_status_display() if hasattr(obj, "get_payment_status_display") else obj.payment_status

        if st == "paid":
            return format_html("<b style='color:#16a34a'>✅ {}</b>", label)
        if st in ("partial", "partially_paid"):
            return format_html("<b style='color:#f59e0b'>🟠 {}</b>", label)
        return format_html("<b style='color:#dc2626'>❌ {}</b>", label or "unpaid")

    payment_badge.short_description = "Paiement"

    def total_client_ttc_display(self, obj):
        val = getattr(obj, "total_client_ttc", None)
        if val is None:
            # fallback ancien
            return obj.total or Decimal("0")
        return val

    total_client_ttc_display.short_description = "Total client TTC"

    def amount_due_display(self, obj):
        total_ttc = getattr(obj, "total_client_ttc", None)
        if total_ttc is None:
            total_ttc = obj.total or Decimal("0")
        paid = obj.amount_paid or Decimal("0")
        due = (Decimal(total_ttc) - Decimal(paid))
        if due <= 0:
            return Decimal("0")
        return due

    amount_due_display.short_description = "Reste à payer"

    def invoice_display(self, obj):
        inv = getattr(obj, "invoice_number", None)
        if not inv:
            return "-"
        return format_html("<code>{}</code>", inv)

    invoice_display.short_description = "Facture"

    def wallets_distributed_display(self, obj):
        val = getattr(obj, "wallets_distributed", False)
        if val:
            return format_html("<b style='color:#16a34a'>✅ Oui</b>")
        return format_html("<span style='color:#6b7280'>—</span>")

    wallets_distributed_display.short_description = "Wallets distribués"

    # ============================================================
    #  LIENS PRATIQUES
    # ============================================================
    def front_detail_link(self, obj):
        """
        Lien vers la fiche front de la commande (/orders/<id>/).
        """
        try:
            url = reverse("orders:detail", args=[obj.pk])
            return format_html('<a href="{}" target="_blank">🔍 Voir fiche</a>', url)
        except Exception:
            return "-"

    front_detail_link.short_description = "Fiche front"

    def customer_wallet_link(self, obj):
        """
        Lien vers le portefeuille du client (wallets:customer_wallet_detail).
        """
        if not obj.customer:
            return "-"
        try:
            url = reverse("wallets:customer_wallet_detail", args=[obj.customer.pk])
            return format_html('<a href="{}" target="_blank">💰 Portefeuille</a>', url)
        except Exception:
            return "-"

    customer_wallet_link.short_description = "Wallet client"

    # ============================================================
    #  BLOCS INFOS
    # ============================================================
    def mlm_info_display(self, obj):
        customer = obj.customer
        if not customer:
            return "Aucun client associé."

        profiles_manager = getattr(customer, "referral_profiles", None)
        profile = profiles_manager.first() if profiles_manager else None

        if not profile:
            return "Aucun profil de parrainage pour ce client."

        sponsor = profile.sponsor
        if sponsor and sponsor.customer:
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
        from wallets.models import Wallet  # import local pour éviter cycles

        customer = obj.customer
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

    # ============================================================
    #  ACTIONS ADMIN (VISION FAGNI)
    # ============================================================
    @admin.action(description="✅ Valider paiement (Cash)")
    def admin_validate_paid_cash(self, request, queryset):
        updated = 0
        skipped = 0

        for order in queryset.select_related("customer"):
            # Si déjà payé, on ignore
            if order.payment_status == "paid":
                skipped += 1
                continue

            # Recalcul d'abord pour avoir total_client_ttc propre
            try:
                order.update_financials(save=False)
            except Exception:
                pass

            total_ttc = getattr(order, "total_client_ttc", None)
            if total_ttc is None:
                total_ttc = order.total or Decimal("0")

            order.payment_status = "paid"
            order.payment_method = "cash"
            order.payment_date = timezone.now()
            order.amount_paid = Decimal(total_ttc)

            # Recalcule + facture (invoice_number) + save() déclenche distribution
            try:
                order.update_financials(save=False)
            except Exception:
                pass

            order.save()
            updated += 1

        # ✅ IMPORTANT : message UNE SEULE fois, après la boucle
        messages.success(
            request,
            f"Paiement CASH validé : {updated} commande(s). Ignorées : {skipped}.",
        )


    @admin.action(description="⚠️ Marquer comme IMPAYÉ (bloqué si wallets distribués)")
    def admin_mark_unpaid(self, request, queryset):
        updated = 0
        blocked = 0

        for order in queryset:
            if getattr(order, "wallets_distributed", False):
                blocked += 1
                continue

            order.payment_status = "unpaid"
            order.amount_paid = Decimal("0")
            order.payment_date = None
            order.payment_reference = None
            order.payment_method = None
            order.invoice_number = None
            order.save()
            updated += 1

        if blocked:
            messages.warning(
                request,
                f"{blocked} commande(s) non modifiée(s) : wallets déjà distribués.",
            )
        messages.success(
            request,
            f"Commandes marquées impayées : {updated}.",
        )

    @admin.action(description="🔄 Recalculer finance (update_financials)")
    def admin_recompute_financials(self, request, queryset):
        ok = 0
        fail = 0
        for order in queryset:
            try:
                order.update_financials(save=True)
                ok += 1
            except Exception:
                fail += 1
        if ok:
            messages.success(request, f"Recalcul OK : {ok} commande(s).")
        if fail:
            messages.error(request, f"Recalcul échoué : {fail} commande(s).")

    def has_change_permission(self, request, obj=None):
        perm = super().has_change_permission(request, obj=obj)
        if not perm:
            return False
        if obj and obj.payment_status == "paid" and not request.user.is_superuser:
            return True  # on autorise l'accès à la fiche
        return True

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj=obj))
        if obj and obj.payment_status == "paid":
            # Tout en read-only quand payé
            # (sauf éventuellement notes ou timeline si tu veux)
            all_fields = [f.name for f in obj._meta.fields]
            ro = list(set(ro + all_fields))
        return ro

    def get_inline_instances(self, request, obj=None):
        if obj and obj.payment_status == "paid":
            return []  # supprime les inlines (OrderItemInline)
        return super().get_inline_instances(request, obj=obj)


# ==============
#  LIGNES & PHOTOS
# ==============
@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "designation", "quantity", "unit_price", "total")
    search_fields = ("designation", "order__code", "order__customer__name")
    list_select_related = ("order",)


@admin.register(OrderItemPhoto)
class OrderItemPhotoAdmin(admin.ModelAdmin):
    list_display = ("order_item", "image", "created_at")
    search_fields = (
        "order_item__designation",
        "order_item__order__code",
        "order_item__order__customer__name",
    )
    list_select_related = ("order_item", "order_item__order")


# ==============
#  CATALOGUE
# ==============
@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name", "slug")
    list_per_page = 50


@admin.register(ServiceItem)
class ServiceItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "default_price", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "code")
    list_per_page = 100


@admin.register(DeliveryLeg)
class DeliveryLegAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "order",
        "leg_type",
        "driver",
        "status",
        "distance_km",
        "client_fee_share",
        "driver_amount",
        "fagni_margin",
        "created_at",
    )
    list_filter = ("leg_type", "status", "driver")
    search_fields = ("order__code", "driver__name")
    autocomplete_fields = ("order", "driver")


class SingletonModelAdmin(admin.ModelAdmin):
    """
    Admin pour les modèles "singleton" :
    - on empêche la suppression
    - on autorise l'ajout seulement si aucune instance n'existe encore
    """

    def has_add_permission(self, request):
        if self.model.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GlobalPricingSettings)
class GlobalPricingSettingsAdmin(SingletonModelAdmin):
    list_display = (
        "service_fee_percent",
        "service_fee_min",
        "delivery_min_fee",
        "delivery_price_per_km",
        "vat_rate",
    )
    fieldsets = (
        ("Service FAGNI", {
            "fields": (
                "service_fee_percent",
                "service_fee_min",
                "rounding_step",
                "apply_service_on_delivery",
            )
        }),
        ("Livraison", {
            "fields": (
                "delivery_min_fee",
                "delivery_price_per_km",
                "delivery_fixed_fee",
                "delivery_free_threshold",
            )
        }),
        ("Supplément express", {
            "fields": (
                "express_extra_fixed",
                "express_extra_percent",
                "express_extra_target",
                "express_affects_customer_price",
            )
        }),
        ("Répartition logistique", {
            "fields": (
                "driver_share_ratio",
                "driver_min_fee",
                "logistic_margin_ratio",
            )
        }),
        ("TVA / fiscalité", {
            "fields": (
                "vat_rate",
                "apply_vat_on_service_only",
            )
        }),
    )


@admin.register(WorkflowSettings)
class WorkflowSettingsAdmin(SingletonModelAdmin):
    list_display = (
        "default_pickup_mode",
        "standard_sla_hours",
        "express_sla_hours",
        "pickup_cutoff_hour",
        "same_day_delivery_allowed",
    )
    fieldsets = (
        ("Modes & SLA", {
            "fields": (
                "default_pickup_mode",
                "standard_sla_hours",
                "express_sla_hours",
                "max_sla_days",
            )
        }),
        ("Enchaînement & règles logistiques", {
            "fields": (
                "pickup_cutoff_hour",
                "same_day_delivery_allowed",
                "require_pickup_done_before_delivery_leg_start",
                "require_laundry_validation_before_delivery_assignment",
            )
        }),
        ("Annulations", {
            "fields": (
                "allow_cancel_after_pickup",
                "cancel_penalty_customer_percent",
                "cancel_penalty_driver_percent",
            )
        }),
    )


@admin.register(AssignmentSettings)
class AssignmentSettingsAdmin(SingletonModelAdmin):
    list_display = (
        "driver_assignment_mode",
        "driver_radius_km",
        "laundry_selection_mode",
        "max_concurrent_orders_per_driver",
    )
    fieldsets = (
        ("Assignation livreurs", {
            "fields": (
                "driver_assignment_mode",
                "max_concurrent_orders_per_driver",
                "driver_radius_km",
                "prefer_same_driver_for_round_trip",
            )
        }),
        ("Sélection blanchisseries", {
            "fields": (
                "laundry_selection_mode",
                "max_daily_orders_per_laundry",
                "laundry_priority_strategy",
            )
        }),
    )


@admin.register(InvoiceSettings)
class InvoiceSettingsAdmin(SingletonModelAdmin):
    list_display = (
        "company_name",
        "company_phone",
        "company_email",
        "updated_at",
    )

    fieldsets = (
        ("En-tête", {
            "fields": (
                "company_name",
                "company_tagline",
                "header_logo",
                "company_address",
                "company_phone",
                "company_email",
                "header_note",
            )
        }),
        ("Pied de page (footer)", {
            "fields": (
                "footer_text",
            )
        }),
    )
