from django.contrib import admin
from .models import LaundryPartner, DeliveryPartner, RelayPointPartner


@admin.register(LaundryPartner)
class LaundryPartnerAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "phone", "is_active")
    list_filter = ("city", "is_active")
    search_fields = ("name", "phone", "address")


@admin.register(DeliveryPartner)
class DeliveryPartnerAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "vehicle_type", "phone", "is_active")
    list_filter = ("city", "vehicle_type", "is_active")
    search_fields = ("name", "phone", "address")


@admin.register(RelayPointPartner)
class RelayPointPartnerAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "relay_type", "phone", "is_active")
    list_filter = ("city", "relay_type", "is_active")
    search_fields = ("name", "phone", "address")
