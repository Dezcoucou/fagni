from django.db import models

class Produit(models.Model):
    nom = models.CharField(max_length=100)
    prix = models.DecimalField(max_digits=10, decimal_places=2)
    categorie = models.CharField(max_length=100, blank=True, null=True)
    actif = models.BooleanField(default=True)

    def __str__(self):
        return self.nom

class Photo(models.Model):
    produit = models.ForeignKey('Produit', on_delete=models.CASCADE, related_name='photos')
    image = models.ImageField(upload_to='produits/')
    date_upload = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Photo de {self.produit.nom}"
