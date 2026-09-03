"""
Gestion de l'expiration des offres Crowd.

Fonctions :
- expire_stale_offers() : expire toutes les offres dépassées
- Peut être appelée par un cron ou un management command
"""
import logging
from django.utils import timezone
from django.db.models import Q

from orders.models import DeliveryLeg
from crowd.dispatch import reject_crowd_offer

logger = logging.getLogger("fagni.crowd.expiration")


def expire_stale_offers() -> dict:
    """
    Expire toutes les offres Crowd dont la date d'expiration est dépassée.
    
    Returns:
        dict avec les statistiques : {expired: int, errors: int}
    """
    now = timezone.now()
    
    # Trouver les offres expirées
    stale_offers = DeliveryLeg.objects.filter(
        status='pending',
        assignment_source='crowd',
        offered_at__isnull=False,
        offer_expires_at__isnull=False,
        offer_expires_at__lte=now,
    )
    
    expired_count = 0
    error_count = 0
    
    for leg in stale_offers:
        try:
            success = reject_crowd_offer(leg, reason="EXPIRED")
            if success:
                expired_count += 1
                logger.info(
                    "Offre Crowd expirée | leg_id=%s | order_id=%s",
                    leg.id, leg.order_id,
                )
        except Exception:
            error_count += 1
            logger.exception(
                "Erreur expiration offre Crowd | leg_id=%s",
                leg.id,
            )
    
    if expired_count > 0:
        logger.info(
            "Expiration offres Crowd terminée | expired=%d | errors=%d",
            expired_count, error_count,
        )
    
    return {"expired": expired_count, "errors": error_count}
