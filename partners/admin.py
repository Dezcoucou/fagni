from django.contrib import admin

from .models import LaundryPartner, DeliveryPartner, RelayPointPartner


@admin.register(LaundryPartner)
class LaundryPartnerAdmin(admin.ModelAdmin):
    """
    Blanchisseries partenaires FAGNI.
    """
    list_display = ("name",)
    search_fields = ("name",)
    list_per_page = 50


@admin.register(DeliveryPartner)
class DeliveryPartnerAdmin(admin.ModelAdmin):
    """
    Livreurs partenaires FAGNI.
    """
    list_display = ("name",)
    search_fields = ("name",)
    list_per_page = 50


@admin.register(RelayPointPartner)
class RelayPointPartnerAdmin(admin.ModelAdmin):
    """
    Points relais partenaires FAGNI.
    """
    list_display = ("name",)
    search_fields = ("name",)
    list_per_page = 50
