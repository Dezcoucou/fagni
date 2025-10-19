from django.db import models

SERVICE_TYPES = [
    ("blanchisserie", "Blanchisserie / Lavage"),
    ("repassage", "Repassage & pliage"),
    ("retouche", "Couture / Retouche"),
    ("cordonnerie", "Cordonnerie / Réparation chaussures"),
]

class Partner(models.Model):
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30)
    email = models.EmailField(blank=True)
    zone = models.CharField(max_length=120)
    service_type = models.CharField(max_length=30, choices=SERVICE_TYPES)
    disponible = models.BooleanField(default=True)
    date_added = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} – {self.get_service_type_display()} ({self.zone})"
