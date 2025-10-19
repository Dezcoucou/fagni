from rest_framework import viewsets, permissions
from .models import Article, ItemPhoto
from .serializers import ArticleSerializer, ItemPhotoSerializer

class ArticleViewSet(viewsets.ModelViewSet):
    queryset = Article.objects.all().order_by("-id")
    serializer_class = ArticleSerializer
    permission_classes = [permissions.AllowAny]

class ItemPhotoViewSet(viewsets.ModelViewSet):
    queryset = ItemPhoto.objects.all().order_by("-id")
    serializer_class = ItemPhotoSerializer
    permission_classes = [permissions.AllowAny]
