from django.contrib import admin
from django.db.models import Sum

from .models import (
    Customer,
    Order,
    OrderItem,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
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

    # On ne les modifie pas à la main : ce sont des champs calculés
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

    def montant_global(self, obj):
        """
        Total global facturé au client :
        prestations TTC + service FAGNI + livraison.
        """
        return obj.grand_total

    montant_global.short_description = "Total global client (FCFA)"


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
