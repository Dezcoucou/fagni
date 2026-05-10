from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Créé le")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Mis à jour le")

    class Meta:
        abstract = True


class Address(TimeStampedModel):
    label = models.CharField(max_length=120, blank=True, verbose_name="Libellé")
    contact_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=30, blank=True, verbose_name="Téléphone")

    country = models.CharField(max_length=80, default="Côte d'Ivoire")
    city = models.CharField(max_length=120, blank=True)
    commune = models.CharField(max_length=120, blank=True)
    district = models.CharField(max_length=120, blank=True)
    street = models.CharField(max_length=255, blank=True)
    landmark = models.CharField(max_length=255, blank=True)

    full_address = models.TextField()
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Latitude")
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True, verbose_name="Longitude")

    instructions = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)

    def __str__(self):
        return self.label or self.full_address[:80]

    class Meta:
        verbose_name = 'Adresse'
        verbose_name_plural = 'Adresses'


class MediaAsset(TimeStampedModel):
    ASSET_TYPE_CHOICES = [
        ("photo", "Photo"),
        ("document", "Document"),
        ("signature", "Signature"),
        ("audio", "Audio"),
        ("other", "Autre"),
    ]

    file = models.FileField(upload_to="media_assets/", verbose_name="Fichier")
    asset_type = models.CharField(max_length=20, choices=ASSET_TYPE_CHOICES, default="photo")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="uploaded_assets",
    )

    def __str__(self):
        return f"{self.asset_type} #{self.pk}"

    class Meta:
        verbose_name = 'Média'
        verbose_name_plural = 'Médias'
