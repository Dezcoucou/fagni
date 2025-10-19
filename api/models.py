from django.db import models


class Article(models.Model):
    title = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title


class ItemPhoto(models.Model):
    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name="photos"
    )
    image = models.ImageField(upload_to="articles/")
    caption = models.CharField(max_length=200, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return f"Photo de {self.article} ({self.pk})"

class Commande(models.Model):
    client_nom = models.CharField(max_length=100)
    client_telephone = models.CharField(max_length=20)
    adresse_collecte = models.CharField(max_length=255)
    date_collecte = models.DateTimeField()
    date_livraison = models.DateTimeField(null=True, blank=True)
    statut = models.CharField(max_length=50, default="En attente")
    montant_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    def __str__(self):
        return f"Commande #{self.id} - {self.client_nom}"
