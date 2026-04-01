from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from .models import Commande

@admin.register(Commande)
class CommandeAdmin(UnfoldModelAdmin):
    list_display = ("code", "nom_client", "telephone", "type_service", "statut", "created_at")
    list_filter = ("type_service", "statut", "created_at")
    search_fields = ("code", "nom_client", "telephone", "zone", "partenaire")
    readonly_fields = ("code", "created_at", "updated_at")