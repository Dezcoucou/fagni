from django.db import models
from core.models import TimeStampedModel


class ServiceCategory(TimeStampedModel):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Service(TimeStampedModel):
    PRICING_MODE_CHOICES = [
        ("per_item", "Par article"),
        ("per_kg", "Par kilo"),
        ("fixed", "Forfait"),
        ("quote_required", "Devis requis"),
        ("hybrid", "Hybride"),
    ]

    code = models.SlugField(max_length=50, unique=True)
    category = models.ForeignKey(
        ServiceCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="services",
    )
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)
    requires_partner = models.BooleanField(default=True)
    requires_logistics = models.BooleanField(default=True)
    requires_weighing = models.BooleanField(default=False)

    pricing_mode = models.CharField(max_length=20, choices=PRICING_MODE_CHOICES, default="per_item")
    default_sla_hours = models.PositiveIntegerField(default=48)

    def __str__(self):
        return self.name


class ServiceOption(TimeStampedModel):
    EXTRA_PRICE_TYPE_CHOICES = [
        ("fixed", "Montant fixe"),
        ("percent", "Pourcentage"),
    ]

    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name="options")
    code = models.SlugField(max_length=50)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    extra_price_type = models.CharField(max_length=20, choices=EXTRA_PRICE_TYPE_CHOICES, default="fixed")
    extra_price_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("service", "code")

    def __str__(self):
        return f"{self.service.name} - {self.name}"
