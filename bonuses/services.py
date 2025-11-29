from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from django.db.models import Q, Sum, Count

from partners.models import DeliveryPartner
from orders.models import Order
from .models import BonusWeek


def compute_weekly_bonus():
    """
    Calcule la prime hebdomadaire de chaque livreur actif.
    Donne un minimum de prime si le livreur a au moins 1 course terminée.
    """
    today = timezone.localdate()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    drivers = DeliveryPartner.objects.filter(is_active=True)

    for driver in drivers:
        # Commandes de la semaine
        qs = Order.objects.filter(
            delivery_partner=driver,
            created_at__date__gte=week_start,
            created_at__date__lte=week_end,
        )

        orders_count = qs.count()
        done_count = qs.filter(status="done").count()
        success_rate = int((done_count / orders_count) * 100) if orders_count else 0

        # Distance totale
        total_distance = qs.aggregate(Sum("distance_km"))["distance_km__sum"] or 0

        # Activité aux heures de pointe
        peak_hours = qs.filter(
            Q(created_at__hour__gte=7, created_at__hour__lt=10)
            | Q(created_at__hour__gte=17, created_at__hour__lt=20)
        ).count()

        # ETA moyen → on laisse à 0 pour l’instant
        avg_eta = Decimal("0.00")

        # ------ CALCUL PRIME ------
        bonus = 0

        # Prime courses
        if orders_count >= 40:
            bonus += 15000
        elif orders_count >= 30:
            bonus += 8000
        elif orders_count >= 20:
            bonus += 4000

        # Prime réussite
        if success_rate >= 90:
            bonus += 5000
        elif success_rate >= 75:
            bonus += 2000

        # Prime heures de pointe
        if peak_hours >= 10:
            bonus += 3000

        # Prime fidélité (4 semaines avec un bonus existant)
        if driver.weekly_bonuses.count() >= 4:
            bonus += 2000

        # 👉 Minimum de prime pour les livreurs actifs
        # Si le livreur a au moins 1 course terminée mais aucun critère rempli
        # on lui donne une petite prime de base (ex : 1000 FCFA)
        if done_count > 0 and bonus == 0:
            bonus = 1000

        BonusWeek.objects.update_or_create(
            driver=driver,
            week_start=week_start,
            defaults={
                "week_end": week_end,
                "orders_count": orders_count,
                "done_count": done_count,
                "success_rate": success_rate,
                "peak_rides": peak_hours,
                "total_distance": total_distance,
                "avg_eta": avg_eta,
                "bonus_amount": bonus,
            },
        )
