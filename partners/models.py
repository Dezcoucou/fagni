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
    Partenaire blanchisserie / pressing FAGNI.
    """
    PARTNER_TYPE_CHOICES = [
        ("blanchisserie", "Blanchisserie"),
        ("pressing",      "Pressing premium"),
    ]
    LEVEL_CHOICES = [
        ("bronze", "🥉 Bronze"),
        ("silver", "🥈 Silver"),
        ("gold",   "🥇 Gold"),
    ]

    partner_type = models.CharField(
        "Type de partenaire",
        max_length=20,
        choices=PARTNER_TYPE_CHOICES,
        default="blanchisserie",
    )
    partner_score = models.PositiveIntegerField(
        "Partner Score",
        default=100,
        help_text="Score automatique sur 100 pts (délai 40 + litiges 30 + dispo 20 + refus 10)"
    )
    level = models.CharField(
        "Niveau",
        max_length=10,
        choices=LEVEL_CHOICES,
        default="bronze",
    )
    score_delai      = models.PositiveIntegerField("Score délai (sur 40)",    default=40)
    score_litiges    = models.PositiveIntegerField("Score litiges (sur 30)",   default=30)
    score_dispo      = models.PositiveIntegerField("Score disponibilité (sur 20)", default=20)
    score_refus      = models.PositiveIntegerField("Score refus (sur 10)",     default=10)
    score_updated_at = models.DateTimeField("Score mis à jour le", null=True, blank=True)

    def update_level(self):
        if self.partner_score >= 80:
            self.level = "gold"
        elif self.partner_score >= 60:
            self.level = "silver"
        else:
            self.level = "bronze"

    def recalculate_score(self):
        self.partner_score = (
            self.score_delai +
            self.score_litiges +
            self.score_dispo +
            self.score_refus
        )
        self.update_level()

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

    is_online = models.BooleanField(
        "En ligne",
        default=False,
        help_text="Le livreur est disponible pour recevoir des missions"
    )
    went_online_at = models.DateTimeField(
        "En ligne depuis", null=True, blank=True
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


class PartnerScoreHistory(models.Model):
    """
    Historique des Partner Scores FAGNI.
    Permet de tracer l'évolution dans le temps.
    """
    partner      = models.ForeignKey(LaundryPartner, on_delete=models.CASCADE, related_name='score_history')
    score        = models.PositiveIntegerField("Score total")
    level        = models.CharField("Niveau", max_length=10)
    score_delai  = models.PositiveIntegerField(default=0)
    score_litiges= models.PositiveIntegerField(default=0)
    score_dispo  = models.PositiveIntegerField(default=0)
    score_refus  = models.PositiveIntegerField(default=0)
    computed_at  = models.DateTimeField("Calculé le", auto_now_add=True)

    class Meta:
        verbose_name = "Historique score partenaire"
        verbose_name_plural = "Historiques scores partenaires"
        ordering = ["-computed_at"]

    def __str__(self):
        return f"{self.partner.name} — {self.score}pts ({self.level}) — {self.computed_at:%d/%m/%Y}"
