from django.db import models
from core.models import TimeStampedModel, Address
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
        ("issue_reported", "Incident signalé"),
    ]

    PRIORITY_CHOICES = [
        ("normal", "Normale"),
        ("high", "Haute"),
        ("urgent", "Urgente"),
    ]

    code = models.CharField(max_length=30, unique=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="v2_missions")

    mission_type = models.CharField(max_length=30, choices=MISSION_TYPE_CHOICES)
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="assigned")

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

    contact_name = models.CharField(max_length=150, blank=True)
    contact_phone = models.CharField(max_length=30, blank=True)

    planned_start_at = models.DateTimeField(null=True, blank=True)
    planned_end_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default="normal")
    instructions = models.TextField(blank=True)
    sequence_index = models.PositiveIntegerField(default=1)

    def __str__(self):
        return self.code


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
    ]

    mission = models.ForeignKey(Mission, on_delete=models.CASCADE, related_name="action_logs")
    action_type = models.CharField(max_length=30, choices=ACTION_TYPE_CHOICES)
    performed_at = models.DateTimeField(auto_now_add=True)

    payload_json = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.mission.code} - {self.action_type}"

from .models_otp import MissionOTP
from .models_signature import MissionSignature
from .models_pdf_log import MissionPdfSendLog
