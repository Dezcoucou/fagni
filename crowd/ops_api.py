"""
API OPS pour la supervision Crowd.

Endpoints :
- GET /api/ops/crowd/offers/ : liste des offres Crowd (en attente + récentes)
- GET /api/ops/crowd/stats/  : métriques Crowd (match rate, acceptation, etc.)
- POST /api/ops/crowd/offers/{leg_id}/force-pro/ : forcer fallback Pro
"""
from datetime import timedelta

from django.db.models import Q, Count, Avg, F
from django.db.models.functions import Now
from django.utils import timezone

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from orders.models import DeliveryLeg, Order
from crowd.models import CrowdSettings, Cotransporter


def _check_ops(request):
    """
    Vérifie que la requête vient d'un utilisateur OPS.
    Réutilise le pattern de orders/ops_api.py.
    """
    import jwt
    from django.conf import settings
    
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        raise ValueError("Token manquant")
    
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
    if not payload.get('ops'):
        raise ValueError("Non autorisé OPS")
    
    return payload


@api_view(['GET'])
@permission_classes([AllowAny])
@authentication_classes([])
def ops_crowd_offers(request):
    """
    GET /api/ops/crowd/offers/
    
    Liste les offres Crowd :
    - En attente (pending + offered_at)
    - Expirées récemment (24h)
    - Acceptées récemment (24h)
    
    Query params :
    - status : pending | expired | accepted | all (défaut: pending)
    - limit : nombre max de résultats (défaut: 50)
    """
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)
    
    status_filter = request.GET.get('status', 'pending')
    limit = min(int(request.GET.get('limit', 50)), 200)
    
    now = timezone.now()
    
    # Queryset de base : offres Crowd
    qs = DeliveryLeg.objects.filter(
        assignment_source='crowd',
    ).select_related(
        'order', 'order__customer', 'cotransporter'
    ).order_by('-offered_at')
    
    # Filtres par statut
    if status_filter == 'pending':
        qs = qs.filter(
            status='pending',
            offered_at__isnull=False,
        ).filter(
            Q(offer_expires_at__isnull=True) | Q(offer_expires_at__gt=now)
        )
    elif status_filter == 'expired':
        qs = qs.filter(
            status='pending',
            offer_expires_at__lte=now,
            offered_at__gte=now - timedelta(hours=24),
        )
    elif status_filter == 'accepted':
        qs = qs.filter(
            status='assigned',
            accepted_at__gte=now - timedelta(hours=24),
        )
    # 'all' : pas de filtre additionnel
    
    offers = []
    for leg in qs[:limit]:
        order = leg.order
        
        # Calcul temps restant / écoulé
        time_status = None
        time_value = None
        if leg.offer_expires_at:
            delta = (leg.offer_expires_at - now).total_seconds()
            if delta > 0:
                time_status = 'expires_in'
                time_value = int(delta)
            else:
                time_status = 'expired_ago'
                time_value = int(-delta)
        elif leg.accepted_at:
            delta = (now - leg.accepted_at).total_seconds()
            time_status = 'accepted_ago'
            time_value = int(delta)
        
        offers.append({
            'leg_id': leg.id,
            'order_code': order.code,
            'order_id': order.id,
            'customer_name': order.customer.name if order.customer else None,
            'customer_phone': order.customer.phone if order.customer else None,
            'leg_type': leg.leg_type,
            'status': leg.status,
            'pickup_address': order.pickup_address or '',
            'delivery_address': order.delivery_address or '',
            'pickup_lat': order.pickup_lat,
            'pickup_lng': order.pickup_lng,
            'scheduled_date': str(order.pickup_scheduled_date) if order.pickup_scheduled_date else None,
            'scheduled_time': str(order.pickup_scheduled_time) if order.pickup_scheduled_time else None,
            'offered_at': leg.offered_at.isoformat() if leg.offered_at else None,
            'accepted_at': leg.accepted_at.isoformat() if leg.accepted_at else None,
            'expires_at': leg.offer_expires_at.isoformat() if leg.offer_expires_at else None,
            'time_status': time_status,
            'time_value': time_value,
            'cotransporter_id': leg.cotransporter_id,
            'cotransporter_name': leg.cotransporter.user.get_full_name() if leg.cotransporter and leg.cotransporter.user else None,
            'amount': float(leg.driver_amount or 0),
            'assignment_reason': leg.assignment_reason or '',
        })
    
    return Response({
        'count': len(offers),
        'status_filter': status_filter,
        'offers': offers,
    })


@api_view(['GET'])
@permission_classes([AllowAny])
@authentication_classes([])
def ops_crowd_stats(request):
    """
    GET /api/ops/crowd/stats/
    
    Métriques Crowd :
    - Total offres (24h, 7j, 30j)
    - Taux d'acceptation
    - Temps moyen d'acceptation
    - Taux d'expiration
    - Nombre de cotransporteurs actifs
    """
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)
    
    now = timezone.now()
    
    # Périodes
    periods = {
        '24h': now - timedelta(hours=24),
        '7d': now - timedelta(days=7),
        '30d': now - timedelta(days=30),
    }
    
    stats = {
        'settings': {
            'crowd_enabled': CrowdSettings.get_solo().crowd_enabled,
            'crowd_priority_over_pro': CrowdSettings.get_solo().crowd_priority_over_pro,
            'pickup_amount': float(CrowdSettings.get_solo().crowd_pickup_amount),
            'return_amount': float(CrowdSettings.get_solo().crowd_return_amount),
            'acceptance_timeout_seconds': CrowdSettings.get_solo().crowd_acceptance_timeout_seconds,
        },
        'periods': {},
        'cotransporters': {
            'total': Cotransporter.objects.count(),
            'active': Cotransporter.objects.filter(is_active=True).count(),
            'verified': Cotransporter.objects.filter(is_verified=True).count(),
        },
    }
    
    for period_name, period_start in periods.items():
        qs = DeliveryLeg.objects.filter(
            assignment_source='crowd',
            offered_at__gte=period_start,
        )
        
        total = qs.count()
        accepted = qs.filter(status='assigned', accepted_at__isnull=False).count()
        expired = qs.filter(
            assignment_reason='EXPIRED',
        ).count()
        rejected = qs.filter(
            assignment_reason='REJECTED_BY_COTRANSPORTER',
        ).count()
        pending = qs.filter(status='pending').count()
        
        # Temps moyen d'acceptation (en secondes)
        avg_acceptance = qs.filter(
            status='assigned',
            accepted_at__isnull=False,
        ).annotate(
            duration=F('accepted_at') - F('offered_at')
        ).aggregate(
            avg_seconds=Avg(F('duration'))
        )['avg_seconds']
        
        # Convertir timedelta en secondes
        avg_seconds = None
        if avg_acceptance:
            avg_seconds = int(avg_acceptance.total_seconds())
        
        stats['periods'][period_name] = {
            'total_offers': total,
            'accepted': accepted,
            'expired': expired,
            'rejected': rejected,
            'pending': pending,
            'acceptance_rate': round(accepted / total * 100, 1) if total > 0 else 0,
            'expiration_rate': round(expired / total * 100, 1) if total > 0 else 0,
            'avg_acceptance_seconds': avg_seconds,
        }
    
    return Response(stats)


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def ops_crowd_force_pro(request, leg_id):
    """
    POST /api/ops/crowd/offers/{leg_id}/force-pro/
    
    Force le fallback Pro pour une offre Crowd en attente.
    Utile si le cotransporteur ne répond pas ou problème logistique.
    """
    try:
        _check_ops(request)
    except Exception:
        return Response({'error': 'Non autorisé'}, status=401)
    
    try:
        leg = DeliveryLeg.objects.select_for_update().get(id=leg_id)
    except DeliveryLeg.DoesNotExist:
        return Response({'error': 'Offre non trouvée'}, status=404)
    
    # Vérifier que c'est bien une offre Crowd en attente
    if leg.assignment_source != 'crowd':
        return Response({'error': 'Cette leg n\'est pas une offre Crowd'}, status=400)
    
    if leg.status != 'pending':
        return Response({'error': f'Leg déjà traitée (status={leg.status})'}, status=400)
    
    # Rejeter l'offre Crowd
    from crowd.dispatch import reject_crowd_offer
    reject_crowd_offer(leg, reason='OPS_FORCE_PRO')
    
    # Tenter fallback Pro
    from orders.assignment import pick_best_driver
    driver, reason = pick_best_driver(leg.order)
    
    if driver:
        from orders.config_models import GlobalPricingSettings
        from decimal import Decimal
        
        driver_amount = Decimal(str(GlobalPricingSettings.get_solo().driver_amount_per_leg))
        
        leg.driver = driver
        leg.driver_amount = driver_amount
        leg.status = 'assigned'
        leg.actor_type = 'professional'
        leg.assignment_source = 'professional'
        leg.assignment_reason = 'OPS_FORCE_PRO_FALLBACK'
        leg.save()
        
        # Mettre à jour order.pickup_driver
        order = leg.order
        order.pickup_driver = driver
        order.cost_driver_pickup = int(driver_amount)
        order.save(update_fields=['pickup_driver', 'cost_driver_pickup'])
        
        return Response({
            'status': 'pro_assigned',
            'leg_id': leg.id,
            'driver_id': driver.id,
            'driver_name': driver.name,
        })
    else:
        return Response({
            'status': 'no_driver_available',
            'leg_id': leg.id,
            'reason': reason or 'Aucun livreur Pro disponible',
        }, status=400)
