"""
Modèles FAGNI Crowd (Cotransportage).

Architecture :
- Cotransporter : acteur non-professionnel qui effectue un trajet déjà prévu
- CotransporterRoute : trajet déclaré par un cotransporteur (origine → destination)
- CrowdSettings : configuration globale du module Crowd (SingletonModel)

Contraintes :
- Réutilise auth.User (pas de duplication de données utilisateur)
- Pas de FK vers DeliveryPartner (monde séparé)
- Payout via WalletTransaction existant (leg.driver_amount)
"""
from django.conf import settings
from django.db import models
from django.utils import timezone
from orders.config_models import SingletonModel


class Cotransporter(models.Model):
    """
    Acteur de cotransportage FAGNI.
    
    Un cotransporteur est un utilisateur vérifié qui déclare des trajets
    et peut accepter des missions de livraison compatibles.
    """
    
    VEHICLE_CHOICES = [
        ("moto", "Moto"),
        ("car", "Voiture"),
        ("bike", "Vélo"),
        ("foot", "À pied"),
        ("other", "Autre"),
    ]
    
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cotransporter_profile",
        verbose_name="Utilisateur",
    )
    
    phone = models.CharField(
        "Téléphone",
        max_length=20,
        blank=True,
        help_text="Numéro de contact (peut différer du compte user)",
    )
    
    is_active = models.BooleanField(
        "Actif",
        default=True,
        help_text="Le cotransporteur peut recevoir des offres de mission",
    )
    
    is_verified = models.BooleanField(
        "Vérifié",
        default=False,
        help_text="Identité et documents vérifiés par OPS",
    )
    
    vehicle_type = models.CharField(
        "Type de véhicule",
        max_length=20,
        choices=VEHICLE_CHOICES,
        blank=True,
    )
    
    capacity_kg = models.DecimalField(
        "Capacité (kg)",
        max_digits=6,
        decimal_places=2,
        default=10.00,
        help_text="Capacité maximale de chargement",
    )
    
    score = models.DecimalField(
        "Score de fiabilité",
        max_digits=5,
        decimal_places=2,
        default=100.00,
        help_text="Score 0-100 basé sur l'historique (missions réussies, retards, etc.)",
    )
    
    total_trips = models.PositiveIntegerField(
        "Total missions proposées",
        default=0,
    )
    
    successful_trips = models.PositiveIntegerField(
        "Missions réussies",
        default=0,
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Cotransporteur"
        verbose_name_plural = "Cotransporteurs"
        ordering = ["-created_at"]
    
    def __str__(self):
        return f"Cotransporter #{self.id} ({self.user})"
    
    @property
    def success_rate(self):
        """Taux de réussite en pourcentage."""
        if self.total_trips == 0:
            return 100.0
        return (self.successful_trips / self.total_trips) * 100
    
    @property
    def is_available(self):
        """Un cotransporteur est disponible s'il est actif ET vérifié."""
        return self.is_active and self.is_verified


class CotransporterRoute(models.Model):
    """
    Trajet déclaré par un cotransporteur.
    
    Un cotransporteur peut déclarer plusieurs routes récurrentes
    (ex: trajet domicile → travail chaque matin).
    """
    
    DAY_CHOICES = [
        ("mon", "Lundi"),
        ("tue", "Mardi"),
        ("wed", "Mercredi"),
        ("thu", "Jeudi"),
        ("fri", "Vendredi"),
        ("sat", "Samedi"),
        ("sun", "Dimanche"),
    ]
    
    cotransporter = models.ForeignKey(
        Cotransporter,
        on_delete=models.CASCADE,
        related_name="routes",
        verbose_name="Cotransporteur",
    )
    
    # Géolocalisation origine
    origin_lat = models.DecimalField(
        "Latitude origine",
        max_digits=9,
        decimal_places=6,
    )
    origin_lng = models.DecimalField(
        "Longitude origine",
        max_digits=9,
        decimal_places=6,
    )
    origin_label = models.CharField(
        "Libellé origine",
        max_length=255,
        blank=True,
        help_text="Ex: 'Domicile - Cocody'",
    )
    
    # Géolocalisation destination
    destination_lat = models.DecimalField(
        "Latitude destination",
        max_digits=9,
        decimal_places=6,
    )
    destination_lng = models.DecimalField(
        "Longitude destination",
        max_digits=9,
        decimal_places=6,
    )
    destination_label = models.CharField(
        "Libellé destination",
        max_length=255,
        blank=True,
        help_text="Ex: 'Travail - Plateau'",
    )
    
    # Temporalité
    departure_time = models.TimeField(
        "Heure de départ",
        help_text="Heure à laquelle le cotransporteur quitte l'origine",
    )
    
    arrival_time = models.TimeField(
        "Heure d'arrivée estimée",
        blank=True,
        null=True,
    )
    
    # Récurrence
    days = models.CharField(
        "Jours de récurrence",
        max_length=20,
        blank=True,
        help_text="Ex: 'mon,tue,wed,thu,fri' (vide = trajet unique)",
    )
    
    valid_from = models.DateField(
        "Valide à partir du",
        default=timezone.now,
    )
    
    valid_until = models.DateField(
        "Valide jusqu'au",
        blank=True,
        null=True,
        help_text="Vide = pas de date de fin",
    )
    
    # Tolérances
    max_detour_km = models.DecimalField(
        "Détour max accepté (km)",
        max_digits=5,
        decimal_places=2,
        default=2.00,
        help_text="Distance maximale que le cotransporteur accepte de dévier",
    )
    
    time_window_minutes = models.PositiveIntegerField(
        "Fenêtre de tolérance (minutes)",
        default=15,
        help_text="Flexibilité sur l'heure de prise en charge",
    )
    
    # État
    is_active = models.BooleanField(
        "Route active",
        default=True,
        help_text="La route est visible pour le matching",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Route cotransporteur"
        verbose_name_plural = "Routes cotransporteurs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["origin_lat", "origin_lng"]),
            models.Index(fields=["destination_lat", "destination_lng"]),
            models.Index(fields=["is_active", "cotransporter"]),
        ]
    
    def __str__(self):
        return f"Route #{self.id} ({self.origin_label} → {self.destination_label})"
    
    def get_days_list(self):
        """Retourne la liste des jours de récurrence."""
        if not self.days:
            return []
        return [d.strip() for d in self.days.split(",") if d.strip()]
    
    def is_valid_on_date(self, date):
        """Vérifie si la route est valide à une date donnée."""
        if date < self.valid_from:
            return False
        if self.valid_until and date > self.valid_until:
            return False
        days_list = self.get_days_list()
        if days_list:
            day_map = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}
            day_code = day_map.get(date.weekday())
            if day_code not in days_list:
                return False
        return True


class CrowdSettings(SingletonModel):
    """
    Configuration globale du module Crowd.
    
    Séparé de AssignmentSettings pour ne pas surcharger la configuration
    existante et permettre une activation/désactivation indépendante.
    """
    
    # Activation globale
    crowd_enabled = models.BooleanField(
        "Module Crowd activé",
        default=False,
        help_text="Active le matching Crowd dans le dispatch",
    )
    
    # Rémunération
    crowd_pickup_amount = models.DecimalField(
        "Rémunération pickup Crowd (FCFA)",
        max_digits=8,
        decimal_places=0,
        default=400,
        help_text="Montant versé au cotransporteur pour une jambe pickup",
    )
    
    crowd_return_amount = models.DecimalField(
        "Rémunération return Crowd (FCFA)",
        max_digits=8,
        decimal_places=0,
        default=400,
        help_text="Montant versé au cotransporteur pour une jambe return",
    )
    
    crowd_minimum_amount = models.DecimalField(
        "Rémunération minimale (FCFA)",
        max_digits=8,
        decimal_places=0,
        default=200,
        help_text="Montant plancher pour toute mission Crowd",
    )
    
    # Matching
    crowd_max_detour_km = models.DecimalField(
        "Détour max global (km)",
        max_digits=5,
        decimal_places=2,
        default=3.00,
        help_text="Plafond global pour le détour accepté",
    )
    
    crowd_acceptance_timeout_seconds = models.PositiveIntegerField(
        "Timeout d'acceptation (secondes)",
        default=120,
        help_text="Durée avant expiration d'une offre de mission",
    )
    
    crowd_min_score = models.DecimalField(
        "Score minimum requis",
        max_digits=5,
        decimal_places=2,
        default=70.00,
        help_text="Score minimum pour qu'un cotransporteur soit éligible",
    )
    
    # Priorité
    crowd_priority_over_pro = models.BooleanField(
        "Crowd prioritaire sur pro",
        default=True,
        help_text="Si True, le matching tente Crowd avant les livreurs pros",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Configuration Crowd"
        verbose_name_plural = "Configuration Crowd"
    
    def __str__(self):
        return "Configuration Crowd FAGNI"
