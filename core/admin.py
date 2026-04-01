from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from .models import Address, MediaAsset


@admin.register(Address)
class AddressAdmin(UnfoldModelAdmin):
    list_display = (
        "id",
        "label",
        "contact_name",
        "phone",
        "city",
        "commune",
        "is_default",
        "created_at",
    )
    search_fields = (
        "label",
        "contact_name",
        "phone",
        "city",
        "commune",
        "district",
        "street",
        "full_address",
    )
    list_filter = ("country", "city", "commune", "is_default", "created_at")
    ordering = ("-created_at",)


@admin.register(MediaAsset)
class MediaAssetAdmin(UnfoldModelAdmin):
    list_display = ("id", "asset_type", "uploaded_by", "created_at")
    search_fields = ("asset_type", "uploaded_by__username", "uploaded_by__email")
    list_filter = ("asset_type", "created_at")
    ordering = ("-created_at",)
