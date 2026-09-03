"""
Notifications FCM pour les cotransporteurs Crowd.

Fonctions :
- notify_cotransporter_of_offer(leg) : notifie quand une offre est créée
- notify_cotransporter_offer_accepted(leg) : notifie quand l'offre est acceptée
- notify_cotransporter_offer_expired(leg) : notifie quand l'offre expire
"""
import logging

from django.conf import settings
from fagni.notifications import send_push

logger = logging.getLogger("fagni.crowd.notifications")


def notify_cotransporter_of_offer(leg, cotransporter=None):
    """
    Notifie le cotransporteur qu'une offre de mission est disponible.
    
    Args:
        leg: DeliveryLeg avec offre Crowd
        cotransporter: Cotransporter à qui l'offre est destinée (optionnel si leg.cotransporter_id existe)
    """
    if not leg:
        return False
    
    # Utiliser le cotransporter passé en paramètre ou celui de la leg
    cotransporter_id = cotransporter.id if cotransporter else getattr(leg, 'cotransporter_id', None)
    if not cotransporter_id:
        return False
    
    try:
        from orders.models import FCMToken
        
        # Récupérer le token FCM du cotransporteur
        token_obj = FCMToken.objects.filter(
            user_type='cotransporter',
            user_id=cotransporter_id,
        ).first()
        
        if not token_obj:
            logger.info(
                "FCM skipped: no token | cotransporter_id=%s | leg_id=%s",
                cotransporter_id, leg.id,
            )
            return False
        
        order = leg.order
        title = "Nouvelle mission disponible"
        body = f"Mission {leg.leg_type} - Commande {order.code}"
        
        data = {
            "type": "crowd_offer",
            "leg_id": str(leg.id),
            "order_code": order.code,
            "leg_type": leg.leg_type,
            "amount": str(leg.driver_amount or 0),
            "pickup_address": order.pickup_address or "",
            "scheduled_date": str(order.pickup_scheduled_date) if order.pickup_scheduled_date else "",
            "scheduled_time": str(order.pickup_scheduled_time) if order.pickup_scheduled_time else "",
        }
        
        success = send_push(
            token=token_obj.token,
            title=title,
            body=body,
            data=data,
        )
        
        if success:
            logger.info(
                "FCM offer sent | cotransporter_id=%s | leg_id=%s | order_code=%s",
                cotransporter_id, leg.id, order.code,
            )
        
        return success
        
    except Exception:
        logger.exception(
            "FCM offer notification failed | leg_id=%s | cotransporter_id=%s",
            leg.id, cotransporter_id,
        )
        return False


def notify_cotransporter_offer_accepted(leg, cotransporter=None):
    """
    Notifie le cotransporteur que son offre a été acceptée (confirmation).
    """
    if not leg:
        return False
    
    cotransporter_id = cotransporter.id if cotransporter else getattr(leg, 'cotransporter_id', None)
    if not cotransporter_id:
        return False
    
    try:
        from orders.models import FCMToken
        
        token_obj = FCMToken.objects.filter(
            user_type='cotransporter',
            user_id=cotransporter_id,
        ).first()
        
        if not token_obj:
            return False
        
        order = leg.order
        title = "Mission confirmée"
        body = f"Commande {order.code} - {leg.leg_type}"
        
        data = {
            "type": "crowd_accepted",
            "leg_id": str(leg.id),
            "order_code": order.code,
        }
        
        return send_push(
            token=token_obj.token,
            title=title,
            body=body,
            data=data,
        )
        
    except Exception:
        logger.exception("FCM accepted notification failed | leg_id=%s", leg.id if leg else None)
        return False


def notify_cotransporter_offer_expired(leg, cotransporter=None):
    """
    Notifie le cotransporteur que son offre a expiré.
    """
    if not leg:
        return False
    
    cotransporter_id = cotransporter.id if cotransporter else getattr(leg, 'cotransporter_id', None)
    if not cotransporter_id:
        return False
    
    try:
        from orders.models import FCMToken
        
        token_obj = FCMToken.objects.filter(
            user_type='cotransporter',
            user_id=cotransporter_id,
        ).first()
        
        if not token_obj:
            return False
        
        order = leg.order
        title = "Mission expirée"
        body = f"Commande {order.code} - offre expirée"
        
        data = {
            "type": "crowd_expired",
            "leg_id": str(leg.id),
            "order_code": order.code,
        }
        
        return send_push(
            token=token_obj.token,
            title=title,
            body=body,
            data=data,
        )
        
    except Exception:
        logger.exception("FCM expired notification failed | leg_id=%s", leg.id if leg else None)
        return False
