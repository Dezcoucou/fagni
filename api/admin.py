from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin
from .models import Article, ItemPhoto


class ItemPhotoInline(admin.TabularInline):
    model = ItemPhoto
    extra = 1


@admin.register(Article)
class ArticleAdmin(UnfoldModelAdmin):
    list_display = ("title", "price", "created_at")
    search_fields = ("title",)
    inlines = [ItemPhotoInline]


@admin.register(ItemPhoto)
class ItemPhotoAdmin(UnfoldModelAdmin):
    list_display = ("article", "caption", "uploaded_at")
    search_fields = ("article__title", "caption")