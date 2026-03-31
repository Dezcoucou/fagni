from django.conf import settings
from django.db import models


class ProofOfDelivery(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("validated", "Validated"),
        ("rejected", "Rejected"),
    ]

    mission = models.ForeignKey(
        "logistics.Mission",
        on_delete=models.CASCADE,
        related_name="delivery_proofs",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
    )

    photo = models.ImageField(upload_to="proofs/", null=True, blank=True)

    otp_code = models.CharField(max_length=10, null=True, blank=True)
    otp_validated = models.BooleanField(default=False)

    notes = models.TextField(blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    validated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Proof {self.id} - Mission {self.mission_id}"
