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
        verbose_name="Partenaire"
    )

    rule_type = models.CharField(max_length=30, choices=RULE_TYPE_CHOICES)
    label = models.CharField(max_length=150, verbose_name="Libellé")

    value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    min_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    max_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    is_active = models.BooleanField(default=True, verbose_name="Actif")
    priority = models.PositiveIntegerField(default=100)

    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.service.name} - {self.label}"

    class Meta:
        verbose_name = 'Règle de prix'
        verbose_name_plural = 'Règles de prix'


class PriceQuote(TimeStampedModel):
    QUOTE_TYPE_CHOICES = [
        ("estimated", "Estimatif"),
        ("revised", "Révisé"),
        ("final", "Final"),
    ]

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="price_quotes_v2", verbose_name="Commande")

    quote_type = models.CharField(max_length=20, choices=QUOTE_TYPE_CHOICES, default="estimated")

    subtotal_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    logistics_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    service_fee = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    currency = models.CharField(max_length=10, default="XOF", verbose_name="Devise")
    is_final = models.BooleanField(default=False)
    generated_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, verbose_name="Notes")

    def __str__(self):
        return f"{self.order.code} - {self.quote_type}"

    class Meta:
        verbose_name = 'Devis'
        verbose_name_plural = 'Devis'



class BagPricingRule(models.Model):
    """
    Règle de pricing pour le mode SAC FAGNI.
    Tout le pricing business doit être paramétrable via l'admin.
    """

    BAG_CODE_CHOICES = [
        ("small", "Petit sac"),
        ("medium", "Sac moyen"),
        ("large", "Grand sac"),
    ]

    code = models.CharField(
        "Code",
        max_length=20,
        choices=BAG_CODE_CHOICES,
        unique=True,
        db_index=True
    )
    label = models.CharField("Libellé", max_length=100)
    price = models.DecimalField("Prix prestation (FCFA)", max_digits=10, decimal_places=2)
    estimated_items = models.PositiveIntegerField("Nombre estimé de pièces", default=0)
    is_active = models.BooleanField("Actif", default=True)
    sort_order = models.PositiveIntegerField("Ordre", default=0)
    notes = models.TextField("Notes internes", blank=True, default="")
    created_at = models.DateTimeField("Créé le", auto_now_add=True)
    updated_at = models.DateTimeField("Mis à jour le", auto_now=True)

    class Meta:
        verbose_name = "Règle pricing sac"
        verbose_name_plural = "Règles pricing sac"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.label} ({self.code}) - {self.price} XOF"

