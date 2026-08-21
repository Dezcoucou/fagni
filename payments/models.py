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

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments_v2", verbose_name="Commande")
    payment_reference = models.CharField(max_length=100, unique=True, verbose_name="Référence paiement")
    payment_method = models.CharField(max_length=30, choices=METHOD_CHOICES, verbose_name="Mode de paiement")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="Statut")

    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Montant")
    currency = models.CharField(max_length=10, default="XOF", verbose_name="Devise")
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Payé le")

    provider_name = models.CharField(max_length=100, blank=True, verbose_name="Nom fournisseur")
    provider_transaction_id = models.CharField(max_length=150, blank=True)
    raw_payload_json = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.payment_reference

    class Meta:
        verbose_name = 'Paiement'
        verbose_name_plural = 'Paiements'


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

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payouts_v2", verbose_name="Commande")
    amount = models.DecimalField(max_digits=12, decimal_places=2, verbose_name="Montant")
    currency = models.CharField(max_length=10, default="XOF", verbose_name="Devise")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", verbose_name="Statut")

    scheduled_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Payé le")
    reference = models.CharField(max_length=100, blank=True, verbose_name="Référence")

    def __str__(self):
        return f"{self.order.code} - {self.beneficiary_type}"

    class Meta:
        verbose_name = 'Versement'
        verbose_name_plural = 'Versements'


class CustomerCharge(TimeStampedModel):
    """
    Créance financière due par un client à FAGNI.

    Ce modèle est volontairement distinct :
    - du total commercial de la commande ;
    - des paiements encaissés ;
    - du wallet client.

    Une créance peut donc rester due même si le wallet client est vide
    et même si la commande source est déjà annulée.
    """

    class ChargeType(models.TextChoices):
        LATE_CANCELLATION = (
            "late_cancellation",
            "Annulation tardive",
        )
        ADJUSTMENT = (
            "adjustment",
            "Ajustement",
        )
        OTHER = (
            "other",
            "Autre",
        )

    class Status(models.TextChoices):
        DUE = "due", "À payer"
        PAID = "paid", "Payée"
        WAIVED = "waived", "Remise / abandonnée"
        CANCELED = "canceled", "Annulée"

    customer = models.ForeignKey(
        "orders.Customer",
        on_delete=models.PROTECT,
        related_name="customer_charges",
        verbose_name="Client",
    )

    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_charges",
        verbose_name="Commande source",
    )

    charge_type = models.CharField(
        "Type de créance",
        max_length=40,
        choices=ChargeType.choices,
    )

    amount = models.DecimalField(
        "Montant",
        max_digits=12,
        decimal_places=2,
    )

    currency = models.CharField(
        "Devise",
        max_length=10,
        default="XOF",
    )

    status = models.CharField(
        "Statut",
        max_length=20,
        choices=Status.choices,
        default=Status.DUE,
        db_index=True,
    )

    reason = models.TextField(
        "Motif",
        blank=True,
        default="",
    )

    idempotency_key = models.CharField(
        "Clé d'idempotence",
        max_length=120,
        unique=True,
    )

    paid_at = models.DateTimeField(
        "Payée le",
        null=True,
        blank=True,
    )

    waived_at = models.DateTimeField(
        "Abandonnée le",
        null=True,
        blank=True,
    )

    class Meta:
        verbose_name = "Créance client"
        verbose_name_plural = "Créances clients"
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=["customer", "status"],
                name="pay_charge_customer_status",
            ),
            models.Index(
                fields=["order", "charge_type"],
                name="pay_charge_order_type",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name="customercharge_amount_gt_zero",
            ),
        ]

    def __str__(self):
        return (
            f"{self.customer} — "
            f"{self.get_charge_type_display()} — "
            f"{self.amount} {self.currency} — "
            f"{self.get_status_display()}"
        )
