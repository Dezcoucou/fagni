from django.db import models
from core.models import TimeStampedModel
from orders.models import Order
from partners.models import LaundryPartner
from services.models import Service


class PricingRule(TimeStampedModel):
    RULE_TYPE_CHOICES = [
        ("per_item", "Par article"),
        ("per_kg", "Par kilo"),
        ("fixed_fee", "Frais fixes"),
        ("express_surcharge", "Majoration express"),
        ("zone_surcharge", "Majoration zone"),
        ("discount", "Remise"),
        ("service_fee", "Frais service"),
        ("commission", "Commission"),
    ]

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="pricing_rules")
    partner = models.ForeignKey(
        LaundryPartner,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pricing_rules_v2",
    )

    rule_type = models.CharField(max_length=30, choices=RULE_TYPE_CHOICES)
    label = models.CharField(max_length=150)

    value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    min_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    is_active = models.BooleanField(default=True)
    priority = models.PositiveIntegerField(default=100)

    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.service.name} - {self.label}"


class PriceQuote(TimeStampedModel):
    QUOTE_TYPE_CHOICES = [
        ("estimated", "Estimatif"),
        ("revised", "Révisé"),
        ("final", "Final"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="price_quotes_v2")

    quote_type = models.CharField(max_length=20, choices=QUOTE_TYPE_CHOICES, default="estimated")

    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    logistics_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    service_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    currency = models.CharField(max_length=10, default="XOF")
    is_final = models.BooleanField(default=False)
    generated_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.order.code} - {self.quote_type}"
