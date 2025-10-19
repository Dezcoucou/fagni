from django.contrib import admin
from .models import Commande

@admin.register(Commande)
class CommandeAdmin(admin.ModelAdmin):
    list_display = ("code", "nom_client", "telephone", "type_service", "statut", "created_at")
    list_filter = ("type_service", "statut", "created_at")
    search_fields = ("code", "nom_client", "telephone", "zone", "partenaire")
    readonly_fields = ("code", "created_at", "updated_at")