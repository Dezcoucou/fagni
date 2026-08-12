from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from core.models import TimeStampedModel
from orders.models import Order
from logistics.models import Mission
from production.models import PartnerJob


class TrackingEvent(TimeStampedModel):
    EVENT_TYPE_CHOICES = [
        ("order_created", "Commande créée"),
        ("mission_assigned", "Mission assignée"),
        ("mission_started", "Mission démarrée"),
        ("pickup_confirmed", "Collecte confirmée"),
        ("partner_received", "Réception partenaire"),
        ("weighing_recorded", "Pesée enregistrée"),
        ("production_started", "Production démarrée"),
        ("production_ready", "Production prête"),
        ("delivery_completed", "Livraison terminée"),
        ("payment_confirmed", "Paiement confirmé"),
        ("issue_reported", "Incident signalé"),
    ]

    ACTOR_ROLE_CHOICES = [
        ("client", "Client"),
        ("driver", "Livreur"),
        ("partner", "Partenaire"),
        ("ops", "Opérations"),
        ("system", "Système"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="tracking_events_v2",
        verbose_name="Commande",
    )

    service_execution = models.ForeignKey(
        "services.ServiceExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracking_events",
        verbose_name="Exécution de service",
    )

    mission = models.ForeignKey(
        Mission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracking_events",
        verbose_name="Mission"
    )
    partner_job = models.ForeignKey(
        PartnerJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracking_events",
        verbose_name="Mission partenaire"
    )

    event_type = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES, verbose_name="Type d'événement")
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracking_events_v2",
        verbose_name="Utilisateur acteur"
    )
    actor_role = models.CharField(max_length=20, choices=ACTOR_ROLE_CHOICES, default="system", verbose_name="Rôle acteur")

    status_before = models.CharField(max_length=50, blank=True, verbose_name="Statut avant")
    status_after = models.CharField(max_length=50, blank=True, verbose_name="Statut après")

    title = models.CharField(max_length=150, verbose_name="Titre")
    description = models.TextField(blank=True, verbose_name="Description")
    metadata_json = models.JSONField(default=dict, blank=True, verbose_name="Métadonnées")

    def _validate_service_execution_contract(self):
        """
        Invariant FAGNI :
        TrackingEvent, Order, Mission, PartnerJob et ServiceExecution
        doivent rester cohérents lorsqu'ils sont renseignés.

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

        if self.service_execution.order_id != self.order_id:
            raise ValidationError(
                {
                    "service_execution": (
                        "Cette exécution de service appartient à une autre "
                        "commande."
                    )
                }
            )

        if (
            self.mission_id is not None
            and self.mission.service_execution_id is not None
            and self.mission.service_execution_id
            != self.service_execution_id
        ):
            raise ValidationError(
                {
                    "mission": (
                        "Cette Mission appartient à une autre "
                        "exécution de service."
                    )
                }
            )

        if (
            self.partner_job_id is not None
            and self.partner_job.service_execution_id is not None
            and self.partner_job.service_execution_id
            != self.service_execution_id
        ):
            raise ValidationError(
                {
                    "partner_job": (
                        "Ce PartnerJob appartient à une autre "
                        "exécution de service."
                    )
                }
            )

    def clean(self):
        super().clean()
        self._validate_service_execution_contract()

    def save(self, *args, **kwargs):
        self._validate_service_execution_contract()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order.code} - {self.event_type}"

    class Meta:
        verbose_name = 'Événement de suivi'
        verbose_name_plural = 'Événements de suivi'


class Proof(TimeStampedModel):
    PROOF_TYPE_CHOICES = [
        ("photo", "Photo"),
        ("signature", "Signature"),
        ("otp", "OTP"),
        ("qr_scan", "QR Scan"),
        ("barcode_scan", "Code-barres"),
        ("receipt", "Reçu"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="proofs_v2", verbose_name="Commande")
    mission = models.ForeignKey(
        Mission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proofs",
        verbose_name="Mission"
    )
    partner_job = models.ForeignKey(
        PartnerJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proofs",
        verbose_name="Mission partenaire"
    )

    proof_type = models.CharField(max_length=30, choices=PROOF_TYPE_CHOICES, verbose_name="Type de preuve")
    file = models.FileField(upload_to="proofs/", null=True, blank=True, verbose_name="Fichier")
    text_value = models.CharField(max_length=255, blank=True, verbose_name="Valeur texte")

    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captured_proofs_v2",
        verbose_name="Capturé par"
    )
    captured_at = models.DateTimeField(auto_now_add=True, verbose_name="Capturé le")
    notes = models.TextField(blank=True, verbose_name="Notes")

    def __str__(self):
        return f"{self.order.code} - {self.proof_type}"

    class Meta:
        verbose_name = 'Preuve'
        verbose_name_plural = 'Preuves'


class Incident(TimeStampedModel):
    INCIDENT_TYPE_CHOICES = [
        ("customer_absent", "Client absent"),
        ("partner_closed", "Partenaire fermé"),
        ("missing_item", "Article manquant"),
        ("wrong_weight", "Poids incorrect"),
        ("damaged_item", "Article endommagé"),
        ("payment_issue", "Problème de paiement"),
        ("address_issue", "Problème d'adresse"),
        ("delay", "Retard"),
        ("delivery_failed", "Livraison échouée"),
    ]

    STATUS_CHOICES = [
        ("open", "Ouvert"),
        ("in_review", "En revue"),
        ("resolved", "Résolu"),
        ("closed", "Clôturé"),
    ]

    SEVERITY_CHOICES = [
        ("low", "Faible"),
        ("medium", "Moyenne"),
        ("high", "Haute"),
        ("critical", "Critique"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="incidents_v2",
        verbose_name="Commande",
    )

    service_execution = models.ForeignKey(
        "services.ServiceExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
        verbose_name="Exécution de service",
    )

    mission = models.ForeignKey(
        Mission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
        verbose_name="Mission"
    )
    partner_job = models.ForeignKey(
        PartnerJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
        verbose_name="Mission partenaire"
    )

    incident_type = models.CharField(max_length=30, choices=INCIDENT_TYPE_CHOICES, verbose_name="Type d'incident")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open", verbose_name="Statut")
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default="medium", verbose_name="Sévérité")

    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_incidents_v2",
        verbose_name="Signalé par"
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_incidents_v2",
        verbose_name="Assigné à"
    )

    title = models.CharField(max_length=150, verbose_name="Titre")
    description = models.TextField(verbose_name="Description")
    resolution_notes = models.TextField(blank=True, verbose_name="Notes")

    reported_at = models.DateTimeField(auto_now_add=True, verbose_name="Signalé le")
    resolved_at = models.DateTimeField(null=True, blank=True, verbose_name="Résolu le")

    def _validate_service_execution_contract(self):
        """
        Invariant FAGNI :
        Incident, Order, Mission, PartnerJob et ServiceExecution
        doivent rester cohérents lorsqu'ils sont renseignés.

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

        if self.service_execution.order_id != self.order_id:
            raise ValidationError(
                {
                    "service_execution": (
                        "Cette exécution de service appartient à une autre "
                        "commande."
                    )
                }
            )

        if (
            self.mission_id is not None
            and self.mission.service_execution_id is not None
            and self.mission.service_execution_id
            != self.service_execution_id
        ):
            raise ValidationError(
                {
                    "mission": (
                        "Cette Mission appartient à une autre "
                        "exécution de service."
                    )
                }
            )

        if (
            self.partner_job_id is not None
            and self.partner_job.service_execution_id is not None
            and self.partner_job.service_execution_id
            != self.service_execution_id
        ):
            raise ValidationError(
                {
                    "partner_job": (
                        "Ce PartnerJob appartient à une autre "
                        "exécution de service."
                    )
                }
            )

    def clean(self):
        super().clean()
        self._validate_service_execution_contract()

    def save(self, *args, **kwargs):
        self._validate_service_execution_contract()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order.code} - {self.incident_type}"

    class Meta:
        verbose_name = 'Incident'
        verbose_name_plural = 'Incidents'
