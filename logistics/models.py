from django.core.exceptions import ValidationError
from django.db import models

from core.models import Address, TimeStampedModel
from orders.models import Order

from .models_proof import ProofOfDelivery


class Mission(TimeStampedModel):
    MISSION_TYPE_CHOICES = [
        ("pickup_from_customer", "Collecte client"),
        ("dropoff_to_partner", "Dépôt partenaire"),
        ("pickup_from_partner", "Récupération partenaire"),
        ("deliver_to_customer", "Livraison client"),
        ("return_mission", "Retour"),
        ("internal_transfer", "Transfert interne"),
    ]

    STATUS_CHOICES = [
        ("assigned", "Assignée"),
        ("accepted", "Acceptée"),
        ("en_route", "En route"),
        ("arrived", "Arrivée"),
        ("in_progress", "En cours"),
        ("awaiting_validation", "En attente de validation"),
        ("completed", "Terminée"),
        ("failed", "Échouée"),
        ("canceled", "Annulée"),
        ("issue_reported", "Incident signalé"),
    ]

    PRIORITY_CHOICES = [
        ("normal", "Normale"),
        ("high", "Haute"),
        ("urgent", "Urgente"),
    ]

    code = models.CharField(
        max_length=30,
        unique=True,
        verbose_name="Code",
    )

    # ---------------------------------------------------------
    # Compatibilité legacy
    # ---------------------------------------------------------
    #
    # Order reste volontairement présent pendant le strangler
    # pattern. Les flux pressing existants peuvent donc continuer
    # à créer des Mission sans ServiceExecution.
    #
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="v2_missions",
        verbose_name="Commande",
    )

    # ---------------------------------------------------------
    # Agrégat opérationnel multiservices
    # ---------------------------------------------------------
    #
    # Nullable pendant la migration progressive.
    #
    # Cible :
    # Order -> ServiceExecution -> Mission(s)
    #
    # Une ServiceExecution peut porter plusieurs missions.
    # La fin d'une Mission ne signifie donc PAS automatiquement
    # la fin de la ServiceExecution.
    #
    service_execution = models.ForeignKey(
        "services.ServiceExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="missions",
        verbose_name="Exécution de service",
    )

    mission_type = models.CharField(
        max_length=30,
        choices=MISSION_TYPE_CHOICES,
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="assigned",
        verbose_name="Statut",
    )

    source_address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_missions",
    )

    destination_address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="destination_missions",
    )

    contact_name = models.CharField(
        max_length=150,
        blank=True,
    )

    contact_phone = models.CharField(
        max_length=30,
        blank=True,
    )

    planned_start_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    planned_end_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    started_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    arrived_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    completed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    failed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    canceled_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Annulée le",
    )

    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default="normal",
    )

    instructions = models.TextField(
        blank=True,
    )

    sequence_index = models.PositiveIntegerField(
        default=1,
    )

    def _validate_service_execution_order(self):
        """
        Invariant FAGNI :
        une Mission et sa ServiceExecution doivent appartenir
        à la même Order.

        service_execution=None reste autorisé pendant le strangler legacy.
        """
        if self.service_execution_id is None:
            return

        if self.order_id is None:
            raise ValidationError(
                {
                    "order": (
                        "Une commande persistée est requise avant de rattacher "
                        "une exécution de service."
                    )
                }
            )

        execution_order_id = self.service_execution.order_id

        if execution_order_id != self.order_id:
            raise ValidationError(
                {
                    "service_execution": (
                        "Cette exécution de service appartient à une autre "
                        "commande."
                    )
                }
            )

    def clean(self):
        super().clean()
        self._validate_service_execution_order()

    def save(self, *args, **kwargs):
        self._validate_service_execution_order()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.code

    class Meta:
        verbose_name = "Mission"
        verbose_name_plural = "Missions"


class MissionActionLog(TimeStampedModel):
    ACTION_TYPE_CHOICES = [
        ("started", "Démarrée"),
        ("arrived", "Arrivée"),
        ("collected", "Collecté"),
        ("handed_over", "Remis"),
        ("otp_verified", "OTP vérifié"),
        ("photo_added", "Photo ajoutée"),
        ("issue_reported", "Incident signalé"),
        ("completed", "Terminée"),
        ("canceled", "Annulée"),
    ]

    mission = models.ForeignKey(
        Mission,
        on_delete=models.CASCADE,
        related_name="action_logs",
        verbose_name="Mission",
    )

    action_type = models.CharField(
        max_length=30,
        choices=ACTION_TYPE_CHOICES,
    )

    performed_at = models.DateTimeField(
        auto_now_add=True,
    )

    payload_json = models.JSONField(
        default=dict,
        blank=True,
    )

    notes = models.TextField(
        blank=True,
        verbose_name="Notes",
    )

    def __str__(self):
        return f"{self.mission.code} - {self.action_type}"

    class Meta:
        verbose_name = "Log de mission"
        verbose_name_plural = "Logs de missions"


from .models_otp import MissionOTP
from .models_signature import MissionSignature
from .models_pdf_log import MissionPdfSendLog
