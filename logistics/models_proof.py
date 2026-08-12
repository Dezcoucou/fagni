from django.conf import settings
from django.core.exceptions import ValidationError
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

    service_execution = models.ForeignKey(
        "services.ServiceExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="delivery_proofs",
        verbose_name="Exécution de service",
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

    def _validate_service_execution_contract(self):
        """
        Invariant FAGNI :

        ProofOfDelivery, Mission, Order et ServiceExecution doivent
        appartenir au même contexte opérationnel.

        service_execution=None reste autorisé pendant la migration legacy.
        """
        if self.service_execution_id is None:
            return

        if self.order_id is None:
            raise ValidationError(
                {
                    "order": (
                        "Une commande persistée est requise avant de rattacher "
                        "une exécution de service."
                    )
                }
            )

        if self.service_execution.order_id != self.order_id:
            raise ValidationError(
                {
                    "service_execution": (
                        "Cette exécution de service appartient à une autre "
                        "commande."
                    )
                }
            )

        if (
            self.mission_id is not None
            and self.mission.order_id != self.order_id
        ):
            raise ValidationError(
                {
                    "mission": (
                        "Cette Mission appartient à une autre commande."
                    )
                }
            )

        if (
            self.mission_id is not None
            and self.mission.service_execution_id is not None
            and self.mission.service_execution_id
            != self.service_execution_id
        ):
            raise ValidationError(
                {
                    "mission": (
                        "Cette Mission appartient à une autre "
                        "exécution de service."
                    )
                }
            )

    def clean(self):
        super().clean()
        self._validate_service_execution_contract()

    def save(self, *args, **kwargs):
        self._validate_service_execution_contract()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Proof {self.id} - Mission {self.mission_id}"
