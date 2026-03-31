from django.db import models


class MissionPdfSendLog(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    ]

    mission = models.ForeignKey(
        "logistics.Mission",
        on_delete=models.CASCADE,
        related_name="pdf_send_logs",
    )
    order = models.ForeignKey(
        "orders.Order",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="pdf_send_logs",
    )

    channel = models.CharField(max_length=30, default="whatsapp")
    recipient = models.CharField(max_length=50, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    provider_message_sid = models.CharField(max_length=80, blank=True)
    error_message = models.TextField(blank=True)
    pdf_url = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PDFLog mission={self.mission_id} status={self.status}"
