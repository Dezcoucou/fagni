from django.db import models

from core.models import TimeStampedModel


class ServiceCategory(TimeStampedModel):
    code = models.SlugField(
        max_length=50,
        unique=True,
        verbose_name="Code",
    )
    name = models.CharField(
        max_length=120,
        verbose_name="Nom",
    )
    description = models.TextField(
        blank=True,
        verbose_name="Description",
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name="Actif",
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Catégorie de service"
        verbose_name_plural = "Catégories de service"


class Service(TimeStampedModel):
    """
    Définition canonique d'un service FAGNI.

    Ce modèle décrit CE QUE FAGNI vend et les capacités nécessaires
    à son exécution.

    Il ne représente ni une commande client, ni une exécution réelle,
    ni une mission terrain.

    Les commandes legacy continuent volontairement à utiliser
    orders.ServiceItem pendant la phase de strangulation.
    """

    PRICING_MODE_CHOICES = [
        ("per_item", "Par article"),
        ("per_kg", "Par kilo"),
        ("fixed", "Forfait"),
        ("quote_required", "Devis requis"),
        ("hybrid", "Hybride"),
    ]

    ENGINE_PICKUP_RETURN = "pickup_return"
    ENGINE_ONSITE = "onsite"
    ENGINE_APPOINTMENT = "appointment"

    EXECUTION_ENGINE_CHOICES = [
        (ENGINE_PICKUP_RETURN, "Collecte / traitement / retour"),
        (ENGINE_ONSITE, "Intervention sur site"),
        (ENGINE_APPOINTMENT, "Rendez-vous"),
    ]

    code = models.SlugField(
        max_length=50,
        unique=True,
        verbose_name="Code",
    )

    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="services",
        verbose_name="Catégorie",
    )

    name = models.CharField(
        max_length=120,
        verbose_name="Nom",
    )

    description = models.TextField(
        blank=True,
        verbose_name="Description",
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Actif",
    )

    # ---------------------------------------------------------
    # Moteur principal d'exécution
    # ---------------------------------------------------------
    primary_engine = models.CharField(
        max_length=30,
        choices=EXECUTION_ENGINE_CHOICES,
        default=ENGINE_PICKUP_RETURN,
        db_index=True,
        verbose_name="Moteur principal",
    )

    # ---------------------------------------------------------
    # Capacités structurelles
    # ---------------------------------------------------------
    requires_partner = models.BooleanField(
        default=True,
        verbose_name="Prestataire requis",
    )

    requires_logistics = models.BooleanField(
        default=True,
        verbose_name="Logistique requise",
    )

    requires_weighing = models.BooleanField(
        default=False,
        verbose_name="Pesée requise",
    )

    requires_appointment = models.BooleanField(
        default=False,
        verbose_name="Rendez-vous requis",
    )

    requires_quote = models.BooleanField(
        default=False,
        verbose_name="Devis requis",
    )

    requires_asset = models.BooleanField(
        default=False,
        verbose_name="Actif / équipement client requis",
        help_text=(
            "Exemple : véhicule pour une vidange. "
            "Le modèle de l'actif sera défini dans un lot dédié."
        ),
    )

    requires_otp = models.BooleanField(
        default=False,
        verbose_name="Validation OTP requise",
    )

    requires_signature = models.BooleanField(
        default=False,
        verbose_name="Signature requise",
    )

    # ---------------------------------------------------------
    # Tarification / SLA
    # ---------------------------------------------------------
    pricing_mode = models.CharField(
        max_length=20,
        choices=PRICING_MODE_CHOICES,
        default="per_item",
        verbose_name="Mode de tarification",
    )

    default_sla_hours = models.PositiveIntegerField(
        default=48,
        verbose_name="SLA par défaut (heures)",
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Service"
        verbose_name_plural = "Services"


class ServiceOption(TimeStampedModel):
    EXTRA_PRICE_TYPE_CHOICES = [
        ("fixed", "Montant fixe"),
        ("percent", "Pourcentage"),
    ]

    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name="options",
    )

    code = models.SlugField(
        max_length=50,
        verbose_name="Code",
    )

    name = models.CharField(
        max_length=120,
        verbose_name="Nom",
    )

    description = models.TextField(
        blank=True,
        verbose_name="Description",
    )

    extra_price_type = models.CharField(
        max_length=20,
        choices=EXTRA_PRICE_TYPE_CHOICES,
        default="fixed",
    )

    extra_price_value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Actif",
    )

    class Meta:
        verbose_name = "Option de service"
        verbose_name_plural = "Options de service"
        unique_together = ("service", "code")

    def __str__(self):
        return f"{self.service.name} - {self.name}"
