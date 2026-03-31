from django.conf import settings
from django.db import models


class MissionSignature(models.Model):
    STATUS_CHOICES = [
        ("captured", "Captured"),
        ("validated", "Validated"),
        ("rejected", "Rejected"),
    ]

    mission = models.ForeignKey(
        "logistics.Mission",
        on_delete=models.CASCADE,
        related_name="signatures",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="signatures",
    )

    signer_name = models.CharField(max_length=120, blank=True)
    signer_role = models.CharField(max_length=50, default="customer", blank=True)

    image = models.ImageField(upload_to="signatures/")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="captured")
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Signature mission={self.mission_id} status={self.status}"
