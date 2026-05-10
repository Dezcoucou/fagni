from django.conf import settings
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

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="tracking_events_v2")
    mission = models.ForeignKey(
        Mission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracking_events",
    )
    partner_job = models.ForeignKey(
        PartnerJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracking_events",
    )

    event_type = models.CharField(max_length=30, choices=EVENT_TYPE_CHOICES)
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracking_events_v2",
    )
    actor_role = models.CharField(max_length=20, choices=ACTOR_ROLE_CHOICES, default="system")

    status_before = models.CharField(max_length=50, blank=True)
    status_after = models.CharField(max_length=50, blank=True)

    title = models.CharField(max_length=150, verbose_name="Titre")
    description = models.TextField(blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.order.code} - {self.event_type}"


class Proof(TimeStampedModel):
    PROOF_TYPE_CHOICES = [
        ("photo", "Photo"),
        ("signature", "Signature"),
        ("otp", "OTP"),
        ("qr_scan", "QR Scan"),
        ("barcode_scan", "Code-barres"),
        ("receipt", "Reçu"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="proofs_v2")
    mission = models.ForeignKey(
        Mission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proofs",
    )
    partner_job = models.ForeignKey(
        PartnerJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="proofs",
    )

    proof_type = models.CharField(max_length=30, choices=PROOF_TYPE_CHOICES)
    file = models.FileField(upload_to="proofs/", null=True, blank=True)
    text_value = models.CharField(max_length=255, blank=True)

    captured_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="captured_proofs_v2",
    )
    captured_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.order.code} - {self.proof_type}"


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

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="incidents_v2")
    mission = models.ForeignKey(
        Mission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
    )
    partner_job = models.ForeignKey(
        PartnerJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incidents",
    )

    incident_type = models.CharField(max_length=30, choices=INCIDENT_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="open")
    severity = models.CharField(
        verbose_name="Sévérité",(max_length=20, choices=SEVERITY_CHOICES, default="medium")

    reported_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_incidents_v2",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_incidents_v2",
    )

    title = models.CharField(max_length=150, verbose_name="Titre")
    description = models.TextField()
    resolution_notes = models.TextField(blank=True)

    reported_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.order.code} - {self.incident_type}"
