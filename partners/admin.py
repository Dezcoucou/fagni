from django.contrib import admin
from django.contrib.admin import ModelAdmin as UnfoldModelAdmin

from .models import LaundryPartner, DeliveryPartner, RelayPointPartner


@admin.register(LaundryPartner)
class LaundryPartnerAdmin(UnfoldModelAdmin):
    """
    Blanchisseries partenaires FAGNI.
    """
    list_display = ("name",)
    search_fields = ("name",)
    list_per_page = 50


@admin.register(DeliveryPartner)
class DeliveryPartnerAdmin(UnfoldModelAdmin):
    """
    Livreurs partenaires FAGNI.
    """
    list_display = ("name",)
    search_fields = ("name",)
    list_per_page = 50


@admin.register(RelayPointPartner)
class RelayPointPartnerAdmin(UnfoldModelAdmin):
    """
    Points relais partenaires FAGNI.
    """
    list_display = ("name",)
    search_fields = ("name",)
    list_per_page = 50
