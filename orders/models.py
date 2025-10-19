from decimal import Decimal, ROUND_HALF_UP
from django.db import models
import uuid


class Customer(models.Model):
    name = models.CharField("Nom", max_length=120)
    phone = models.CharField("Téléphone", max_length=30, blank=True)
    email = models.EmailField("Email", blank=True)
    address = models.CharField("Adresse", max_length=255, blank=True)

    class Meta:
        verbose_name = "Client"
        verbose_name_plural = "Clients"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.phone})" if self.phone else self.name


class Order(models.Model):

    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    STATUS_CHOICES = [
        ("pending", "En attente"),
        ("in_progress", "En cours"),
        ("done", "Terminée"),
        ("canceled", "Annulée"),
    ]

    code = models.CharField("Code", max_length=20, unique=True, blank=True)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name="orders", verbose_name="Client")
    status = models.CharField("Statut", max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField("Créée le", auto_now_add=True)
    updated_at = models.DateTimeField("Mise à jour le", auto_now=True)

    class Meta:
        verbose_name = "Commande"
        verbose_name_plural = "Commandes"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.code or 'SANS-CODE'} - {self.customer}"

    @property

    def total_ht(self):

        total = sum((li.line_total for li in self.items.all()), Decimal("0.00"))

        return total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)



    @property

    def tva_amount(self):

        # TVA par défaut à 0%. Mets Decimal("0.18") si 18%.

        tva_rate = Decimal("0.00")

        return (self.total_ht * tva_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)



    @property

    def total_ttc(self):

        return (self.total_ht + self.tva_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)



    @property

    def amount_paid(self):

        paid = sum((p.amount for p in self.payments.filter(status="paid")), Decimal("0.00"))

        return paid.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)



    @property

    def amount_due(self):

        return (self.total_ttc - self.amount_paid).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)



    def save(self, *args, **kwargs):
        if not self.code:
            self.code = str(uuid.uuid4())[:8]

        super().save(*args, **kwargs)

        from django.db.models import Sum

        total = self.items.aggregate(s=Sum("total"))["s"] or Decimal("0.00")

        type(self).objects.filter(pk=self.pk).update(total=total)



class OrderItem(models.Model):
    @property
    def line_total(self):
        q = self.quantity or Decimal("0")
        p = self.unit_price or Decimal("0")
        return (q * p).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items", verbose_name="Commande")
    designation = models.CharField("Désignation", max_length=120)  # ex: Chemise, Pantalon…
    quantity = models.PositiveIntegerField("Quantité", default=1)
    unit_price = models.DecimalField("Prix unitaire (FCFA)", max_digits=10, decimal_places=2)

    class Meta:
        verbose_name = "Ligne de commande"
        verbose_name_plural = "Lignes de commande"

    def __str__(self):
        return f"{self.designation} x{self.quantity}"

