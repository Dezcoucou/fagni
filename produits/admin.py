from django.contrib import admin
from .models import Produit, Photo

class PhotoInline(admin.TabularInline):
    model = Photo
    extra = 1

@admin.register(Produit)
class ProduitAdmin(admin.ModelAdmin):
    inlines = [PhotoInline]
    list_display = ('nom', 'prix', 'categorie', 'actif')
