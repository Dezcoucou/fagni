from django.contrib import admin

from .models import (
    Customer,
    Order,
    OrderItem,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
)
from partners.models import LaundryPartner, DeliveryPartner


# ─────────────────────────────
#  Catalogue de services
# ─────────────────────────────

@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ServiceItem)
class ServiceItemAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "default_price", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("name", "code")
    autocomplete_fields = ("category",)


# ─────────────────────────────
#  Inlines pour les commandes
# ─────────────────────────────

class OrderItemPhotoInline(admin.TabularInline):
    """
    Photos liées à une ligne de commande.
    Géré dans l'écran d'édition de la ligne.
    """
    model = OrderItemPhoto
    extra = 1


class OrderItemInline(admin.TabularInline):
    """
    Lignes de commande dans l'écran de la commande.
    (Une ligne = un article / une prestation)
    """
    model = OrderItem
    extra = 0
    show_change_link = True


# ─────────────────────────────
#  Admin Client
# ─────────────────────────────

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "address")
    search_fields = ("name", "phone", "email", "address")
    ordering = ("name",)


# ─────────────────────────────
#  Admin Commande
# ─────────────────────────────

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "customer",
        "laundry_partner",
        "delivery_partner",
        "status",
        "total",
        "service_fee_fcfa",
        "delivery_fee_fcfa",
        "grand_total_fcfa",
        "created_at",
    )
    list_filter = (
        "status",
        "laundry_partner",
        "delivery_partner",
        "created_at",
    )
    search_fields = (
        "code",
        "customer__name",
        "customer__phone",
        "laundry_partner__name",
        "delivery_partner__name",
    )
    date_hierarchy = "created_at"
    inlines = [OrderItemInline]
    autocomplete_fields = ("customer", "laundry_partner", "delivery_partner")

    def service_fee_fcfa(self, obj):
        return f"{obj.service_fee:.0f} FCFA"
    service_fee_fcfa.short_description = "Service FAGNI"

    def delivery_fee_fcfa(self, obj):
        # delivery_fee peut ne pas exister dans les vieilles migrations,
        # ou être None → on sécurise.
        fee = getattr(obj, "delivery_fee", 0) or 0
        try:
            fee_val = float(fee)
        except Exception:
            fee_val = 0
        return f"{fee_val:.0f} FCFA"
    delivery_fee_fcfa.short_description = "Livraison"

    def grand_total_fcfa(self, obj):
        return f"{obj.grand_total:.0f} FCFA"
    grand_total_fcfa.short_description = "Total général"


# ─────────────────────────────
#  Admin Ligne de commande
# ─────────────────────────────

@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ("order", "designation", "quantity", "unit_price", "total")
    search_fields = (
        "designation",
        "order__code",
        "order__customer__name",
    )
    list_filter = ("order__status",)
    inlines = [OrderItemPhotoInline]


# ─────────────────────────────
#  Admin Photos d’articles
# ─────────────────────────────

@admin.register(OrderItemPhoto)
class OrderItemPhotoAdmin(admin.ModelAdmin):
    list_display = ("id", "order_item")
    search_fields = ("order_item__order__code", "order_item__designation")
