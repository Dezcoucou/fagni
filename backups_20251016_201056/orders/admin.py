from django.contrib import admin
from .models import Order

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("code", "client", "phone", "status", "created_at")
    search_fields = ("code", "client", "phone")
    list_filter = ("status", "created_at")
