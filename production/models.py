from django.db import models
from core.models import TimeStampedModel
from orders.models import Order
from partners.models import LaundryPartner
from logistics.models import Mission


class PartnerJob(TimeStampedModel):
    STATUS_CHOICES = [
        ("awaiting_reception", "En attente de réception"),
        ("received", "Reçu"),
        ("weighed", "Pesé"),
        ("confirmed", "Confirmé"),
        ("processing", "En traitement"),
        ("ready", "Prêt"),
        ("handed_over", "Remis au livreur"),
        ("issue", "Incident"),
    ]

    code = models.CharField(max_length=30, unique=True)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="partner_jobs_v2")
    partner = models.ForeignKey(LaundryPartner, on_delete=models.CASCADE, related_name="jobs_v2")

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="awaiting_reception")

    received_at = models.DateTimeField(null=True, blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    handed_over_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True)

    def __str__(self):
        return self.code


class WeighingRecord(TimeStampedModel):
    WEIGHING_STAGE_CHOICES = [
        ("pickup_estimate", "Estimation à la collecte"),
        ("partner_reception", "Réception partenaire"),
        ("final_validation", "Validation finale"),
    ]

    ROLE_CHOICES = [
        ("driver", "Livreur"),
        ("partner", "Partenaire"),
        ("ops", "Opérations"),
        ("system", "Système"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="weighing_records_v2")
    partner_job = models.ForeignKey(
        PartnerJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weighing_records",
    )
    mission = models.ForeignKey(
        Mission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weighing_records",
    )

    performed_by_role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="driver")
    weighing_stage = models.CharField(max_length=30, choices=WEIGHING_STAGE_CHOICES)

    gross_weight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    net_weight = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=10, default="kg")

    notes = models.TextField(blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.order_id} - {self.net_weight}{self.unit}"
