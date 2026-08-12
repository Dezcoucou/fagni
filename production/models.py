from django.core.exceptions import ValidationError
from django.db import models
from core.models import TimeStampedModel
from orders.models import Order
from partners.models import LaundryPartner
from logistics.models import Mission


class PartnerJob(TimeStampedModel):
    STATUS_CHOICES = [
        ("awaiting_reception", "En attente de réception"),
        ("received", "Reçu"),
        ("weighed", "Pesé"),
        ("confirmed", "Confirmé"),
        ("processing", "En traitement"),
        ("ready", "Prêt"),
        ("handed_over", "Remis au livreur"),
        ("issue", "Incident"),
    ]

    code = models.CharField(
        max_length=30,
        unique=True,
        verbose_name="Code",
    )

    # ---------------------------------------------------------
    # Compatibilité legacy
    # ---------------------------------------------------------
    #
    # Order reste présent pendant le strangler pattern.
    # Les flux historiques peuvent donc continuer à créer
    # des PartnerJob sans ServiceExecution.
    #
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="partner_jobs_v2",
        verbose_name="Commande",
    )

    # ---------------------------------------------------------
    # Agrégat opérationnel multiservices
    # ---------------------------------------------------------
    #
    # Cible :
    # Order -> ServiceExecution -> PartnerJob(s)
    #
    # Nullable pendant la migration progressive.
    #
    service_execution = models.ForeignKey(
        "services.ServiceExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="partner_jobs",
        verbose_name="Exécution de service",
    )

    partner = models.ForeignKey(
        LaundryPartner,
        on_delete=models.CASCADE,
        related_name="jobs_v2",
        verbose_name="Partenaire",
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="awaiting_reception",
        verbose_name="Statut",
    )

    received_at = models.DateTimeField(null=True, blank=True)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    handed_over_at = models.DateTimeField(null=True, blank=True)

    notes = models.TextField(blank=True, verbose_name="Notes")

    def _validate_service_execution_order(self):
        """
        Invariant FAGNI :
        un PartnerJob et sa ServiceExecution doivent appartenir
        à la même Order.

        service_execution=None reste autorisé pendant le strangler legacy.
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

        execution_order_id = self.service_execution.order_id

        if execution_order_id != self.order_id:
            raise ValidationError(
                {
                    "service_execution": (
                        "Cette exécution de service appartient à une autre "
                        "commande."
                    )
                }
            )

    def clean(self):
        super().clean()
        self._validate_service_execution_order()

    def save(self, *args, **kwargs):
        self._validate_service_execution_order()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.code

    class Meta:
        verbose_name = "Mission partenaire"
        verbose_name_plural = "Missions partenaires"


class WeighingRecord(TimeStampedModel):
    WEIGHING_STAGE_CHOICES = [
        ("pickup_estimate", "Estimation à la collecte"),
        ("partner_reception", "Réception partenaire"),
        ("final_validation", "Validation finale"),
    ]

    ROLE_CHOICES = [
        ("driver", "Livreur"),
        ("partner", "Partenaire"),
        ("ops", "Opérations"),
        ("system", "Système"),
    ]

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="weighing_records_v2",
        verbose_name="Commande",
    )

    service_execution = models.ForeignKey(
        "services.ServiceExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weighing_records",
        verbose_name="Exécution de service",
    )

    partner_job = models.ForeignKey(
        PartnerJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weighing_records",
        verbose_name="Mission partenaire"
    )
    mission = models.ForeignKey(
        Mission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weighing_records",
        verbose_name="Mission"
    )

    performed_by_role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="driver")
    weighing_stage = models.CharField(max_length=30, choices=WEIGHING_STAGE_CHOICES)

    gross_weight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    net_weight = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=10, default="kg")

    notes = models.TextField(blank=True, verbose_name="Notes")
    recorded_at = models.DateTimeField(auto_now_add=True)

    def _validate_service_execution_order(self):
        """
        Invariant FAGNI :
        une WeighingRecord et sa ServiceExecution doivent appartenir
        à la même Order.

        Les rattachements Mission / PartnerJob, lorsqu'ils existent,
        doivent également être cohérents avec ServiceExecution.
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
            self.partner_job_id is not None
            and self.partner_job.service_execution_id is not None
            and self.partner_job.service_execution_id
            != self.service_execution_id
        ):
            raise ValidationError(
                {
                    "partner_job": (
                        "Ce PartnerJob appartient à une autre "
                        "exécution de service."
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
        self._validate_service_execution_order()

    def save(self, *args, **kwargs):
        self._validate_service_execution_order()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.order_id} - {self.net_weight}{self.unit}"

    class Meta:
        verbose_name = 'Pesée'
        verbose_name_plural = 'Pesées'
