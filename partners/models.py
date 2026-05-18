from django.db import models


class PartnerBase(models.Model):
    """
    Classe de base pour tous les partenaires FAGNI :
    - blanchisseries
    - livreurs
    - points relais
    """
    name = models.CharField("Nom", max_length=150)
    phone = models.CharField("Téléphone", max_length=50, blank=True)
    email = models.EmailField("Email", blank=True)
    address = models.CharField("Adresse", max_length=255, blank=True)

    # Coordonnées GPS (pour la logistique FAGNI)
    latitude = models.DecimalField(
        "Latitude",
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True
    )
    longitude = models.DecimalField(
        "Longitude",
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True
    )

    city = models.CharField("Ville / Zone", max_length=120, blank=True)
    remuneration_collecte  = models.PositiveIntegerField("Rémunération collecte (FCFA)", default=1000)
    remuneration_livraison = models.PositiveIntegerField("Rémunération livraison (FCFA)", default=1000)
    wave_number = models.CharField("Numéro Wave", max_length=20, blank=True)
    notes = models.TextField("Notes internes", blank=True)
    is_active = models.BooleanField("Actif", default=True)

    created_at = models.DateTimeField("Créé le", auto_now_add=True)
    updated_at = models.DateTimeField("Mis à jour le", auto_now=True)

    class Meta:
        abstract = True
        ordering = ["name"]


class LaundryPartner(PartnerBase):
    """
    Partenaire blanchisserie FAGNI.
    """

    class Meta:
        verbose_name = "Blanchisserie partenaire"
        verbose_name_plural = "Blanchisseries partenaires"

    def __str__(self):
        return f"{self.name} ({self.city})" if self.city else self.name


class DeliveryPartner(PartnerBase):
    """
    Partenaire livreur FAGNI.
    """
    VEHICLE_CHOICES = [
        ("moto", "Moto"),
        ("car", "Voiture"),
        ("bike", "Vélo"),
        ("other", "Autre"),
    ]

    vehicle_type = models.CharField(
        "Type de véhicule",
        max_length=20,
        choices=VEHICLE_CHOICES,
        blank=True,
    )

    class Meta:
        verbose_name = "Livreur partenaire"
        verbose_name_plural = "Livreurs partenaires"

    def __str__(self):
        return f"{self.name} ({self.city})" if self.city else self.name


class RelayPointPartner(PartnerBase):
    """
    Point relais FAGNI : boutique, pressing partenaire, kiosque qui sert
    uniquement de point de dépôt / retrait pour les clients.
    """

    RELAY_TYPE_CHOICES = [
        ("shop", "Boutique / supérette"),
        ("pressing", "Pressing partenaire"),
        ("kiosk", "Kiosque"),
        ("other", "Autre"),
    ]

    relay_type = models.CharField(
        "Type de point relais",
        max_length=20,
        choices=RELAY_TYPE_CHOICES,
        blank=True,
    )

    opening_hours = models.CharField(
        "Horaires d'ouverture",
        max_length=120,
        blank=True,
    )

    class Meta:
        verbose_name = "Point relais partenaire"
        verbose_name_plural = "Points relais partenaires"

    def __str__(self):
        if self.city:
            return f"{self.name} - {self.city} (Point relais)"
        return f"{self.name} (Point relais)"
