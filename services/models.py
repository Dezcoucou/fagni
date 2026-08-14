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
        ("bag", "Par sac"),
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


class CustomerAsset(TimeStampedModel):
    """
    Actif / équipement appartenant à un client FAGNI.

    Exemples :
    - véhicule ;
    - climatiseur ;
    - machine ;
    - équipement professionnel ;
    - autre bien nécessitant une intervention.

    Le modèle reste volontairement générique afin de ne pas enfermer
    le moteur multiservices dans un domaine métier particulier.
    """

    ASSET_TYPE_OTHER = "other"
    ASSET_TYPE_VEHICLE = "vehicle"
    ASSET_TYPE_EQUIPMENT = "equipment"
    ASSET_TYPE_MACHINE = "machine"

    ASSET_TYPE_CHOICES = [
        (ASSET_TYPE_VEHICLE, "Véhicule"),
        (ASSET_TYPE_EQUIPMENT, "Équipement"),
        (ASSET_TYPE_MACHINE, "Machine"),
        (ASSET_TYPE_OTHER, "Autre"),
    ]

    customer = models.ForeignKey(
        "orders.Customer",
        on_delete=models.PROTECT,
        related_name="service_assets",
        verbose_name="Client",
    )

    asset_type = models.CharField(
        max_length=30,
        choices=ASSET_TYPE_CHOICES,
        default=ASSET_TYPE_OTHER,
        db_index=True,
        verbose_name="Type d'actif",
    )

    name = models.CharField(
        max_length=150,
        verbose_name="Nom / désignation",
    )

    reference = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="Référence",
        help_text=(
            "Exemple : immatriculation, numéro de série ou référence client."
        ),
    )

    description = models.TextField(
        blank=True,
        verbose_name="Description",
    )

    metadata_json = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Métadonnées",
        help_text=(
            "Données descriptives variables selon le type d'actif. "
            "Les informations critiques ou fréquemment filtrées devront "
            "devenir des champs dédiés."
        ),
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Actif",
    )

    def __str__(self):
        reference = f" · {self.reference}" if self.reference else ""
        return f"{self.customer} · {self.name}{reference}"

    class Meta:
        verbose_name = "Actif client"
        verbose_name_plural = "Actifs clients"
        ordering = ("customer_id", "name", "id")
        indexes = [
            models.Index(
                fields=("customer", "asset_type"),
                name="cust_asset_customer_type_idx",
            ),
        ]


class ServiceExecution(TimeStampedModel):
    """
    Instance d'exécution d'un Service pour une commande FAGNI.

    Service décrit CE QUI peut être vendu.
    ServiceExecution décrit CE QUI est réellement en train d'être exécuté.

    Ce modèle est volontairement indépendant des modèles métier spécialisés :
    - aucune dépendance directe à LaundryPartner ;
    - aucune dépendance directe à DeliveryPartner ;
    - aucune dépendance obligatoire à Mission ;
    - aucune logique pressing spécifique.

    Pendant la migration progressive du legacy, Order reste l'agrégat commercial
    et financier existant tandis que ServiceExecution devient l'agrégat
    opérationnel multiservices.

    Le moteur est figé au moment de la création afin de préserver l'historique,
    même si la définition du Service change ultérieurement.
    """

    STATUS_PENDING = "pending"
    STATUS_SCHEDULED = "scheduled"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_AWAITING_VALIDATION = "awaiting_validation"
    STATUS_COMPLETED = "completed"
    STATUS_CANCELED = "canceled"
    STATUS_FAILED = "failed"

    STATUS_CHOICES = [
        (STATUS_PENDING, "En attente"),
        (STATUS_SCHEDULED, "Planifiée"),
        (STATUS_IN_PROGRESS, "En cours"),
        (STATUS_AWAITING_VALIDATION, "En attente de validation"),
        (STATUS_COMPLETED, "Terminée"),
        (STATUS_CANCELED, "Annulée"),
        (STATUS_FAILED, "Échouée"),
    ]

    ENGINE_PICKUP_RETURN = Service.ENGINE_PICKUP_RETURN
    ENGINE_ONSITE = Service.ENGINE_ONSITE
    ENGINE_APPOINTMENT = Service.ENGINE_APPOINTMENT

    EXECUTION_ENGINE_CHOICES = Service.EXECUTION_ENGINE_CHOICES

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="service_executions",
        verbose_name="Commande",
    )

    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="executions",
        verbose_name="Service",
    )


    asset = models.ForeignKey(
        CustomerAsset,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="service_executions",
        verbose_name="Actif client",
        help_text=(
            "Actif concerné par cette exécution lorsque le service "
            "requiert un équipement client."
        ),
    )

    execution_engine = models.CharField(
        max_length=30,
        choices=EXECUTION_ENGINE_CHOICES,
        db_index=True,
        verbose_name="Moteur d'exécution",
        help_text=(
            "Snapshot du moteur utilisé pour cette exécution. "
            "Ne doit pas dépendre dynamiquement de Service.primary_engine."
        ),
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
        verbose_name="Statut",
    )

    sequence_index = models.PositiveIntegerField(
        default=1,
        verbose_name="Ordre d'exécution",
        help_text=(
            "Permet à une commande de contenir plusieurs exécutions "
            "dans un ordre déterminé."
        ),
    )

    planned_start_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Début planifié",
    )

    planned_end_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fin planifiée",
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Début réel",
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Fin réelle",
    )

    canceled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Annulée le",
    )

    metadata_json = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Métadonnées métier",
        help_text=(
            "Données métier non structurelles et évolutives. "
            "Les données fréquemment filtrées ou critiques doivent devenir "
            "des colonnes ou modèles dédiés."
        ),
    )

    service_snapshot_json = models.JSONField(
        default=dict,
        blank=True,
        verbose_name="Snapshot du service",
        help_text=(
            "Capture optionnelle de la configuration du service au moment "
            "de la création de l'exécution."
        ),
    )

    notes = models.TextField(
        blank=True,
        verbose_name="Notes internes",
    )

    def _validate_asset_customer(self):
        """
        Invariant FAGNI :
        l'actif d'une ServiceExecution doit appartenir au client
        propriétaire de la commande.

        asset=None reste autorisé :
        - pour les services qui n'exigent pas d'actif ;
        - pendant la migration progressive du legacy.
        """
        if self.asset_id is None:
            return

        if self.order_id is None:
            from django.core.exceptions import ValidationError

            raise ValidationError(
                {
                    "order": (
                        "Une commande persistée est requise avant "
                        "de rattacher un actif."
                    )
                }
            )

        if self.asset.customer_id != self.order.customer_id:
            from django.core.exceptions import ValidationError

            raise ValidationError(
                {
                    "asset": (
                        "Cet actif appartient à un autre client "
                        "que celui de la commande."
                    )
                }
            )

    def clean(self):
        super().clean()
        self._validate_asset_customer()

    def save(self, *args, **kwargs):
        self._validate_asset_customer()
        return super().save(*args, **kwargs)

    def __str__(self):
        order_code = getattr(self.order, "code", None) or f"#{self.order_id}"
        return f"{order_code} · {self.service.name} · {self.status}"

    class Meta:
        verbose_name = "Exécution de service"
        verbose_name_plural = "Exécutions de services"
        ordering = ("order_id", "sequence_index", "id")
        indexes = [
            models.Index(
                fields=("order", "status"),
                name="svc_exec_order_status_idx",
            ),
            models.Index(
                fields=("service", "status"),
                name="svc_exec_service_status_idx",
            ),
            models.Index(
                fields=("execution_engine", "status"),
                name="svc_exec_engine_status_idx",
            ),
        ]
