from django.conf import settings
from django.db import models


class MissionOTP(models.Model):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("sent", "Sent"),
        ("approved", "Approved"),
        ("failed", "Failed"),
        ("expired", "Expired"),
        ("canceled", "Canceled"),
    ]

    mission = models.ForeignKey(
        "logistics.Mission",
        on_delete=models.CASCADE,
        related_name="otp_records",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        related_name="otp_records",
    )

    phone_number = models.CharField(max_length=30)
    channel = models.CharField(max_length=20, default="whatsapp")
    provider = models.CharField(max_length=30, default="twilio_messaging")

    otp_code = models.CharField(max_length=10, blank=True)
    message_sid = models.CharField(max_length=64, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")

    attempts = models.PositiveIntegerField(default=0)
    sent_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    last_error = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"OTP mission={self.mission_id} status={self.status}"
