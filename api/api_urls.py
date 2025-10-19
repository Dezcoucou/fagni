from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .api_views import ArticleViewSet, ItemPhotoViewSet

router = DefaultRouter()
router.register(r'articles', ArticleViewSet, basename='article')
router.register(r'item-photos', ItemPhotoViewSet, basename='itemphoto')

urlpatterns = [
    path('', include(router.urls)),
]
