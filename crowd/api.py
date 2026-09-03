"""
API Cotransporteur Crowd.

Endpoints :
- GET  /api/crowd/offers/          : liste des offres disponibles
- POST /api/crowd/offers/{id}/accept : accepter une offre
- POST /api/crowd/offers/{id}/reject : rejeter une offre
- GET  /api/crowd/missions/        : liste des missions acceptées

Authentification : JWT (cotransporter_id dans le payload)
"""
import jwt
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.db.models import Q

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from crowd.models import Cotransporter, CrowdSettings
from crowd.dispatch import accept_crowd_offer, reject_crowd_offer
from orders.models import DeliveryLeg


def _authenticate_cotransporter(request):
    """
    Authentifie un cotransporteur via JWT.
    Retourne (cotransporter, error_response).
    """
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        return None, Response({'error': 'Token manquant'}, status=401)
    
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
        cotransporter_id = payload.get('cotransporter_id') or payload.get('cid')
        if not cotransporter_id:
            return None, Response({'error': 'cotransporter_id manquant dans le token'}, status=401)
        
        cotransporter = Cotransporter.objects.get(id=cotransporter_id, is_active=True)
        return cotransporter, None
    except jwt.ExpiredSignatureError:
        return None, Response({'error': 'Token expiré'}, status=401)
    except jwt.InvalidTokenError:
        return None, Response({'error': 'Token invalide'}, status=401)
    except Cotransporter.DoesNotExist:
        return None, Response({'error': 'Cotransporteur non trouvé'}, status=404)
    except Exception:
        return None, Response({'error': 'Erreur d\'authentification'}, status=401)


@api_view(['GET'])
@permission_classes([AllowAny])
@authentication_classes([])
def api_crowd_offers(request):
    """
    GET /api/crowd/offers/
    
    Retourne les offres Crowd disponibles pour le cotransporteur authentifié.
    Filtre par :
    - Offres non expirées
    - Offres non encore acceptées
    - Offres géographiquement compatibles (optionnel, basé sur les routes)
    """
    cotransporter, error = _authenticate_cotransporter(request)
    if error:
        return error
    
    now = timezone.now()
    
    # Offres disponibles : pending, avec offre active, non expirées
    offers = DeliveryLeg.objects.filter(
        status='pending',
        assignment_source='crowd',
        offered_at__isnull=False,
    ).filter(
        Q(offer_expires_at__isnull=True) | Q(offer_expires_at__gt=now)
    ).select_related('order', 'order__customer').order_by('-offered_at')
    
    result = []
    for leg in offers:
        order = leg.order
        result.append({
            'offer_id': leg.id,
            'order_code': order.code,
            'leg_type': leg.leg_type,
            'pickup_address': order.pickup_address or '',
            'delivery_address': order.delivery_address or '',
            'pickup_lat': order.pickup_lat,
            'pickup_lng': order.pickup_lng,
            'delivery_lat': order.delivery_lat,
            'delivery_lng': order.delivery_lng,
            'scheduled_date': str(order.pickup_scheduled_date) if order.pickup_scheduled_date else None,
            'scheduled_time': str(order.pickup_scheduled_time) if order.pickup_scheduled_time else None,
            'offered_at': leg.offered_at.isoformat() if leg.offered_at else None,
            'expires_at': leg.offer_expires_at.isoformat() if leg.offer_expires_at else None,
            'amount': float(
                CrowdSettings.get_solo().crowd_pickup_amount if leg.leg_type == 'pickup'
                else CrowdSettings.get_solo().crowd_return_amount
            ),
        })
    
    return Response({
        'count': len(result),
        'offers': result,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def api_crowd_accept_offer(request, leg_id):
    """
    POST /api/crowd/offers/{leg_id}/accept
    
    Accepte une offre Crowd.
    """
    cotransporter, error = _authenticate_cotransporter(request)
    if error:
        return error
    
    try:
        leg = DeliveryLeg.objects.get(id=leg_id)
    except DeliveryLeg.DoesNotExist:
        return Response({'error': 'Offre non trouvée'}, status=404)
    
    success, reason = accept_crowd_offer(leg, cotransporter)
    
    if success:
        return Response({
            'status': 'accepted',
            'mission_id': leg.id,
            'order_code': leg.order.code,
            'leg_type': leg.leg_type,
            'amount': float(leg.driver_amount),
        })
    else:
        status_code = 409 if reason == 'ALREADY_ASSIGNED' else 400
        return Response({
            'status': 'rejected',
            'reason': reason,
        }, status=status_code)


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def api_crowd_reject_offer(request, leg_id):
    """
    POST /api/crowd/offers/{leg_id}/reject
    
    Rejette une offre Crowd.
    """
    cotransporter, error = _authenticate_cotransporter(request)
    if error:
        return error
    
    try:
        leg = DeliveryLeg.objects.get(id=leg_id)
    except DeliveryLeg.DoesNotExist:
        return Response({'error': 'Offre non trouvée'}, status=404)
    
    success = reject_crowd_offer(leg, reason="REJECTED_BY_COTRANSPORTER")
    
    if success:
        return Response({
            'status': 'rejected',
            'offer_id': leg.id,
        })
    else:
        return Response({
            'status': 'already_processed',
            'offer_id': leg.id,
        }, status=400)


@api_view(['GET'])
@permission_classes([AllowAny])
@authentication_classes([])
def api_crowd_missions(request):
    """
    GET /api/crowd/missions/
    
    Retourne les missions acceptées par le cotransporteur.
    """
    cotransporter, error = _authenticate_cotransporter(request)
    if error:
        return error
    
    missions = DeliveryLeg.objects.filter(
        actor_type='cotransporter',
        cotransporter=cotransporter,
    ).select_related('order', 'order__customer').order_by('-accepted_at')
    
    result = []
    for leg in missions:
        order = leg.order
        result.append({
            'mission_id': leg.id,
            'order_code': order.code,
            'leg_type': leg.leg_type,
            'status': leg.status,
            'pickup_address': order.pickup_address or '',
            'delivery_address': order.delivery_address or '',
            'amount': float(leg.driver_amount),
            'accepted_at': leg.accepted_at.isoformat() if leg.accepted_at else None,
        })
    
    return Response({
        'count': len(result),
        'missions': result,
    })


@api_view(['POST'])
@permission_classes([AllowAny])
@authentication_classes([])
def api_crowd_save_fcm_token(request):
    """
    POST /api/crowd/fcm-token/
    
    Enregistre le token FCM du cotransporteur authentifié.
    """
    cotransporter, error = _authenticate_cotransporter(request)
    if error:
        return error
    
    token = request.data.get('token')
    if not token:
        return Response({'error': 'token requis'}, status=400)
    
    from orders.models import FCMToken
    
    # Un même token ne doit pas être attaché à plusieurs profils
    FCMToken.objects.filter(token=token).exclude(
        user_type='cotransporter',
        user_id=cotransporter.id,
    ).delete()
    
    FCMToken.objects.update_or_create(
        user_type='cotransporter',
        user_id=cotransporter.id,
        defaults={'token': token},
    )
    
    return Response({'status': 'ok'})
