from django.conf import settings
from django.db import models
from core.models import TimeStampedModel, Address


class UserRole(TimeStampedModel):
    ROLE_CHOICES = [
        ("client", "Client"),
        ("driver", "Livreur"),
        ("partner_manager", "Responsable partenaire"),
        ("ops_admin", "Administrateur opérations"),
        ("superadmin", "Super administrateur"),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="roles", verbose_name="Utilisateur")
    role = models.CharField(max_length=30, choices=ROLE_CHOICES)
    is_primary = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'Rôle utilisateur'
        verbose_name_plural = 'Rôles utilisateurs'
        unique_together = ("user", "role")

    def __str__(self):
        return f"{self.user} - {self.role}"


class CustomerProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="customer_profile", verbose_name="Utilisateur")
    default_address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_default_for",
    )
    preferred_contact_mode = models.CharField(max_length=30, blank=True)
    notes = models.TextField(blank=True, verbose_name="Notes")

    def __str__(self):
        return f"Client: {self.user}"

    class Meta:
        verbose_name = 'Profil client'
        verbose_name_plural = 'Profils clients'


class DriverProfile(TimeStampedModel):
    VEHICLE_TYPE_CHOICES = [
        ("bike", "Moto"),
        ("car", "Voiture"),
        ("van", "Fourgonnette"),
        ("foot", "À pied"),
        ("other", "Autre"),
    ]

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="driver_profile", verbose_name="Utilisateur")
    display_name = models.CharField(max_length=150, blank=True)
    phone_number = models.CharField(max_length=30, blank=True)

    vehicle_type = models.CharField(max_length=20, choices=VEHICLE_TYPE_CHOICES, default="bike")
    vehicle_plate = models.CharField(max_length=30, blank=True)
    zone = models.CharField(max_length=120, blank=True)

    is_available = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False, verbose_name="Vérifié")
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0, verbose_name="Note")
    notes = models.TextField(blank=True, verbose_name="Notes")

    def __str__(self):
        return self.display_name or str(self.user)

    class Meta:
        verbose_name = 'Profil livreur'
        verbose_name_plural = 'Profils livreurs'
