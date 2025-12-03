from decimal import Decimal

from django.contrib import admin
from django.utils.html import format_html
from django.db.models import Sum
from django.urls import reverse

from .models import (
    Customer,
    Order,
    OrderItem,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
    DeliveryLeg,
)


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
        """
        Nombre total de commandes du client.
        """
        return obj.orders.count()

    nb_commandes.short_description = "Nb commandes"

    def montant_total(self, obj):
        """
        Montant total des prestations TTC de ce client (somme Order.total).
        """
        agg = obj.orders.aggregate(total=Sum("total"))
        return agg["total"] or 0

    montant_total.short_description = "Montant total (FCFA)"


# ==============
#  COMMANDES
# ==============
@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "customer",
        "status",
        "total",
        "service_fee",
        "delivery_fee",
        "distance_km",
        "montant_global",
        "front_detail_link",       # lien vers fiche front
        "customer_wallet_link",    # lien vers portefeuille client
        "created_at",
    )
    list_filter = (
        "status",
        "laundry_partner",
        "delivery_partner",
        "relay_partner",
        "created_at",
    )
    search_fields = (
        "code",
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

    # Champs calculés / read-only
    readonly_fields = (
        "created_at",
        "updated_at",
        "pickup_time",
        "dropoff_time",
        "wash_complete_time",
        "return_time",
        "delivered_time",
        "total",
        "service_fee",
        "distance_km",
        "delivery_fee",
        "driver_logistic_cost",
        "logistic_margin",
        # blocs d’info custom
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
                    "created_at",
                    "updated_at",
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
            "Montants",
            {
                "fields": (
                    "total",
                    "service_fee",
                    "delivery_fee",
                    "distance_km",
                    "driver_logistic_cost",
                    "logistic_margin",
                )
            },
        ),
        (
            "Programme Parrainage & Portefeuille",
            {
                "fields": (
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

    # --- Total TTC calculé ---

    def montant_global(self, obj):
        """
        Montant TTC complet (prestations + service FAGNI + livraison + TVA).

        compute_totals(save=False) prépare les attributs calculés (dont grand_total)
        sans modifier la BD.
        """
        try:
            obj.compute_totals(save=False)
        except Exception:
            # Si compute_totals n'existe pas ou échoue, fallback sur total
            return obj.total or Decimal("0.00")

        return getattr(obj, "grand_total", obj.total or Decimal("0.00"))

    montant_global.short_description = "Total TTC"
    montant_global.admin_order_field = "total"

    # --- Liens pratiques dans la liste admin ---

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


    # --- Bloc d’info MLM dans la fiche commande ---
    def mlm_info_display(self, obj):
        """
        Affiche les infos de parrainage du client (profil affilié + parrain)
        sous forme de texte simple + lien.
        """
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

        # Lien vers la fiche affilié MLM
        try:
            affiliate_url = reverse("mlm:affiliate_detail", args=[profile.referral_code])
            link = format_html(
                " – <a href='{}' target='_blank'>Voir fiche affilié MLM</a>",
                affiliate_url,
            )
        except Exception:
            link = ""

        # Affichage compact : tout sur une ligne
        return format_html(
            "Code affilié client : {} – {}{}",
            profile.referral_code,
            sponsor_txt,
            link,
        )

    mlm_info_display.short_description = "Programme Parrainage (MLM)"


    # --- Bloc d’info Portefeuille client dans la fiche commande ---
    def wallet_info_display(self, obj):
        """
        Affiche un résumé du portefeuille FAGNI du client (texte + lien).
        """
        from wallets.models import Wallet  # import local pour éviter les cycles

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
                wallet_url = reverse(
                    "wallets:customer_wallet_detail",
                    args=[customer.pk],
                )
                link = format_html(
                    " – <a href='{}' target='_blank'>Voir portefeuille client</a>",
                    wallet_url,
                )
            except Exception:
                link = ""
        else:
            link = " – Aucun portefeuille ouvert pour ce client."

        return format_html(
            "Solde portefeuille client : {} FCFA{}",
            solde,
            link,
        )

    wallet_info_display.short_description = "Portefeuille client"


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
