from rest_framework import serializers
from .models import Article, ItemPhoto

class ItemPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ItemPhoto
        fields = ["id", "article", "image", "created_at"]

class ArticleSerializer(serializers.ModelSerializer):
    photos = ItemPhotoSerializer(source="itemphoto_set", many=True, read_only=True)

    class Meta:
        model = Article
        fields = ["id", "title", "description", "price_est", "created_at", "photos"]
