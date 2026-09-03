"""
Moteur d'Analytics pour le module Crowd.

Fournit des KPIs business pour le pilotage opérationnel.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Avg, Count, F, Sum, Q
from django.db.models.functions import ExtractDay, TruncDate
from django.utils import timezone

from orders.models import DeliveryLeg, Order
from crowd.models import CrowdSettings, Cotransporter


def get_crowd_kpis(days=30):
    """
    Calcule les KPIs Crowd sur une période donnée.
    
    Args:
        days (int): Nombre de jours à analyser (défaut: 30)
    
    Returns:
        dict: KPIs calculés
    """
    now = timezone.now()
    start_date = now - timedelta(days=days)
    
    # Filtrer les legs Crowd sur la période
    qs = DeliveryLeg.objects.filter(
        assignment_source='crowd',
        offered_at__gte=start_date,
    )
    
    total_offers = qs.count()
    
    if total_offers == 0:
        return {
            'period_days': days,
            'total_offers': 0,
            'message': 'Aucune donnée Crowd sur cette période',
        }
    
    # 1. Taux d'acceptation
    accepted = qs.filter(status='assigned', accepted_at__isnull=False).count()
    acceptance_rate = round((accepted / total_offers) * 100, 1)
    
    # 2. Taux d'expiration
    expired = qs.filter(assignment_reason='EXPIRED').count()
    expiration_rate = round((expired / total_offers) * 100, 1)
    
    # 3. Taux de rejet manuel
    rejected = qs.filter(assignment_reason='REJECTED_BY_COTRANSPORTER').count()
    rejection_rate = round((rejected / total_offers) * 100, 1)
    
    # 4. Temps moyen d'acceptation (en minutes)
    avg_acceptance_seconds = qs.filter(
        status='assigned',
        accepted_at__isnull=False,
    ).annotate(
        duration=F('accepted_at') - F('offered_at')
    ).aggregate(
        avg=Avg('duration')
    )['avg']
    
    avg_acceptance_minutes = None
    if avg_acceptance_seconds:
        avg_acceptance_minutes = round(avg_acceptance_seconds.total_seconds() / 60, 1)
    
    # 5. Économies réalisées (différence entre coût Pro et coût Crowd)
    # On compare driver_amount (coût réel Crowd) avec le coût Pro standard
    from orders.config_models import GlobalPricingSettings
    pro_cost_per_leg = Decimal(str(GlobalPricingSettings.get_solo().driver_amount_per_leg))
    
    total_crowd_cost = qs.filter(
        status='assigned'
    ).aggregate(
        total=Sum('driver_amount')
    )['total'] or Decimal('0')
    
    # Économie = (Coût Pro * nb acceptés) - Coût Crowd réel
    theoretical_pro_cost = pro_cost_per_leg * accepted
    savings = theoretical_pro_cost - total_crowd_cost
    
    # 6. Répartition par type de jambe
    pickup_count = qs.filter(leg_type='pickup').count()
    return_count = qs.filter(leg_type='return').count()
    
    # 7. Top cotransporteurs (par nombre d'acceptations)
    top_cotransporters = list(
        qs.filter(status='assigned')
        .values('cotransporter__user__username')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )
    
    return {
        'period_days': days,
        'total_offers': total_offers,
        'accepted': accepted,
        'expired': expired,
        'rejected': rejected,
        'acceptance_rate': acceptance_rate,
        'expiration_rate': expiration_rate,
        'rejection_rate': rejection_rate,
        'avg_acceptance_minutes': avg_acceptance_minutes,
        'savings_fcfa': int(savings),
        'theoretical_pro_cost_fcfa': int(theoretical_pro_cost),
        'actual_crowd_cost_fcfa': int(total_crowd_cost),
        'pickup_count': pickup_count,
        'return_count': return_count,
        'top_cotransporters': [
            {'username': t['cotransporter__user__username'] or 'Inconnu', 'count': t['count']}
            for t in top_cotransporters
        ],
    }


def get_crowd_daily_trend(days=14):
    """
    Retourne l'évolution quotidienne des offres Crowd.
    
    Args:
        days (int): Nombre de jours (défaut: 14)
    
    Returns:
        list: Données journalières pour graphiques
    """
    now = timezone.now()
    start_date = now - timedelta(days=days)
    
    qs = DeliveryLeg.objects.filter(
        assignment_source='crowd',
        offered_at__gte=start_date,
    )
    
    # Agrégation par jour
    daily_stats = qs.annotate(
        date=TruncDate('offered_at')
    ).values('date').annotate(
        total=Count('id'),
        accepted=Count('id', filter=Q(status='assigned')),
        expired=Count('id', filter=Q(assignment_reason='EXPIRED')),
    ).order_by('date')
    
    return list(daily_stats)
