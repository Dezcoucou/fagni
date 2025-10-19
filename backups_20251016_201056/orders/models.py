from django.db import models

class Order(models.Model):
    STATUS_CHOICES = [
        ('pending', 'En attente'),
        ('in_progress', 'En cours'),
        ('done', 'Terminée'),
    ]

    code = models.CharField(max_length=20, unique=True)
    client = models.CharField(max_length=100)
    phone = models.CharField(max_length=30, blank=True)
    item = models.CharField(max_length=100)                 # Article
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} - {self.client}"

    @property
    def total(self):
        return self.unit_price * self.quantity
