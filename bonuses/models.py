from django.db import models
from django.utils import timezone
from partners.models import DeliveryPartner


class BonusWeek(models.Model):
    """
    Prime hebdomadaire d’un livreur.
    Ex : Semaine du 2025-11-24 au 2025-11-30
    """

    driver = models.ForeignKey(
        DeliveryPartner,
        on_delete=models.CASCADE,
        related_name="weekly_bonuses"
    )

    week_start = models.DateField()
    week_end = models.DateField()

    orders_count = models.IntegerField(default=0)
    done_count = models.IntegerField(default=0)
    success_rate = models.IntegerField(default=0)

    peak_rides = models.IntegerField(default=0)
    total_distance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    avg_eta = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    bonus_amount = models.IntegerField(default=0)  # Prime totale FCFA

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("driver", "week_start")
        ordering = ["-week_start"]

    def __str__(self):
        return f"Prime {self.driver.name} - semaine {self.week_start}"
