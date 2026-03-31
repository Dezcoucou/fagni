from django.db import models
from core.models import TimeStampedModel
from orders.models import Order
from partners.models import LaundryPartner, DeliveryPartner


class Payment(TimeStampedModel):
    STATUS_CHOICES = [
        ("pending", "En attente"),
        ("authorized", "Autorisé"),
        ("paid", "Payé"),
        ("failed", "Échoué"),
        ("refunded", "Remboursé"),
    ]

    METHOD_CHOICES = [
        ("cash", "Espèces"),
        ("mobile_money", "Mobile Money"),
        ("card", "Carte"),
        ("wallet", "Wallet"),
        ("transfer", "Virement"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments_v2")
    payment_reference = models.CharField(max_length=100, unique=True)
    payment_method = models.CharField(max_length=30, choices=METHOD_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="XOF")
    paid_at = models.DateTimeField(null=True, blank=True)

    provider_name = models.CharField(max_length=100, blank=True)
    provider_transaction_id = models.CharField(max_length=150, blank=True)
    raw_payload_json = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.payment_reference


class Payout(TimeStampedModel):
    STATUS_CHOICES = [
        ("pending", "En attente"),
        ("scheduled", "Programmé"),
        ("paid", "Payé"),
        ("failed", "Échoué"),
    ]

    BENEFICIARY_TYPE_CHOICES = [
        ("partner", "Partenaire"),
        ("driver", "Livreur"),
    ]

    beneficiary_type = models.CharField(max_length=20, choices=BENEFICIARY_TYPE_CHOICES)
    beneficiary_partner = models.ForeignKey(
        LaundryPartner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts_v2",
    )
    beneficiary_driver = models.ForeignKey(
        DeliveryPartner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payouts_v2",
    )

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payouts_v2")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=10, default="XOF")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    scheduled_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.order.code} - {self.beneficiary_type}"
