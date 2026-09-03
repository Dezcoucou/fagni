"""
Dispatch hybride Crowd → Pro → OPS pour les DeliveryLeg.

Architecture :
- dispatch_delivery_leg(leg) : cascade principale
- accept_crowd_offer(leg, cotransporter) : acceptation atomique
- reject_crowd_offer(leg) : rejet/expiration d'offre

Garanties :
- Atomicité via select_for_update()
- Pas de double attribution
- Fallback automatique Pro si Crowd échoue
- Fallback OPS si Pro échoue
- Traçabilité complète (source, reason, timestamps)
"""
from datetime import timedelta
from typing import Tuple, Optional
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from crowd.models import CrowdSettings
from crowd.matching import pick_best_cotransporter
from orders.assignment import pick_best_driver
from crowd.notifications import notify_cotransporter_of_offer


def dispatch_delivery_leg(leg) -> Tuple[Optional[object], str, str]:
    """
    Cascade d'assignation : Crowd → Pro → OPS.
    
    Args:
        leg: DeliveryLeg à assigner
    
    Returns:
        Tuple (actor, reason, mode) où :
        - actor : Cotransporter | DeliveryPartner | None
        - reason : code standardisé (MATCHED, DETOUR_TOO_HIGH, etc.)
        - mode : "offered" | "professional" | "ops"
    
    Notes:
        - Si Crowd match → crée une offre (pas encore une assignation)
        - Si Crowd échoue → fallback Pro immédiat
        - Si Pro échoue → mode OPS (non assigné)
    """
    settings = CrowdSettings.get_solo()
    
    # 1. Tentative Crowd
    if settings.crowd_enabled and settings.crowd_priority_over_pro:
        try:
            crowd, reason = pick_best_cotransporter(leg)
            if crowd:
                # Créer une offre (pas encore une assignation)
                with transaction.atomic():
                    leg_locked = type(leg).objects.select_for_update().get(pk=leg.pk)
                    
                    # Vérifier que la leg est encore disponible
                    if leg_locked.status not in ("pending",):
                        return (None, "ALREADY_DISPATCHED", "ops")
                    
                    leg_locked.assignment_source = "crowd"
                    leg_locked.assignment_reason = reason
                    leg_locked.offered_at = timezone.now()
                    leg_locked.offer_expires_at = timezone.now() + timedelta(
                        seconds=settings.crowd_acceptance_timeout_seconds
                    )
                    leg_locked.save(update_fields=[
                        "assignment_source",
                        "assignment_reason",
                        "offered_at",
                        "offer_expires_at",
                    ])
                
                leg.refresh_from_db()
                
                # Notifier le cotransporteur de l'offre
                try:
                    notify_cotransporter_of_offer(leg, crowd)
                except Exception:
                    import logging
                    logging.getLogger("fagni.crowd.dispatch").exception(
                        "Erreur notification offre | leg_id=%s", leg.id
                    )
                
                return (crowd, reason, "offered")
        except Exception as e:
            import logging
            logging.getLogger("fagni.crowd.dispatch").exception(
                "Erreur Crowd dispatch | leg_id=%s | error=%s",
                leg.id, str(e)
            )
            # Continuer vers Pro si Crowd échoue
    
    # 2. Fallback Pro
    try:
        pro, reason = pick_best_driver(leg.order)
        if pro:
            with transaction.atomic():
                leg_locked = type(leg).objects.select_for_update().get(pk=leg.pk)
                
                # Vérifier que la leg est encore disponible
                if leg_locked.status not in ("pending",):
                    return (None, "ALREADY_DISPATCHED", "ops")
                
                leg_locked.driver = pro
                leg_locked.actor_type = "professional"
                leg_locked.status = "assigned"
                leg_locked.assignment_source = "professional"
                leg_locked.assignment_reason = reason or "MATCHED"
                leg_locked.save()
            
            leg.refresh_from_db()
            return (pro, reason or "MATCHED", "professional")
    except Exception as e:
        import logging
        logging.getLogger("fagni.crowd.dispatch").exception(
            "Erreur Pro dispatch | leg_id=%s | error=%s",
            leg.id, str(e)
        )
    
    # 3. OPS (non assigné)
    with transaction.atomic():
        leg_locked = type(leg).objects.select_for_update().get(pk=leg.pk)
        leg_locked.assignment_source = "ops"
        leg_locked.assignment_reason = reason or "NO_MATCH"
        leg_locked.save(update_fields=["assignment_source", "assignment_reason"])
    
    leg.refresh_from_db()
    return (None, reason or "NO_MATCH", "ops")


def accept_crowd_offer(leg, cotransporter) -> Tuple[bool, str]:
    """
    Acceptation atomique d'une offre Crowd.
    
    Args:
        leg: DeliveryLeg avec offre en cours
        cotransporter: Cotransporter qui accepte
    
    Returns:
        Tuple (success, reason) où :
        - success : bool
        - reason : "ACCEPTED" | "ALREADY_ASSIGNED" | "NO_OFFER" | "OFFER_EXPIRED" | "WRONG_ACTOR"
    """
    from orders.models import DeliveryLeg
    
    with transaction.atomic():
        leg_locked = DeliveryLeg.objects.select_for_update().get(pk=leg.pk)
        
        # Guards
        if leg_locked.status not in ("pending",):
            return (False, "ALREADY_ASSIGNED")
        
        # Vérifier que la leg n'est pas déjà assignée à un autre cotransporteur
        if leg_locked.cotransporter_id is not None:
            return (False, "ALREADY_ASSIGNED")
        
        if not leg_locked.offered_at:
            return (False, "NO_OFFER")
        
        if leg_locked.offer_expires_at and leg_locked.offer_expires_at < timezone.now():
            return (False, "OFFER_EXPIRED")
        
        if leg_locked.assignment_source != "crowd":
            return (False, "WRONG_ACTOR")
        
        # Assignation
        settings = CrowdSettings.get_solo()
        amount = (
            settings.crowd_pickup_amount if leg_locked.leg_type == "pickup"
            else settings.crowd_return_amount
        )
        
        leg_locked.actor_type = "cotransporter"
        leg_locked.cotransporter = cotransporter
        leg_locked.status = "assigned"
        leg_locked.accepted_at = timezone.now()
        leg_locked.driver_amount = amount
        leg_locked.save()
        leg.refresh_from_db()
        
        return (True, "ACCEPTED")


def reject_crowd_offer(leg, reason="REJECTED") -> bool:
    """
    Rejet ou expiration d'une offre Crowd.
    
    Args:
        leg: DeliveryLeg avec offre en cours
        reason: "REJECTED" | "EXPIRED" | "CANCELED"
    
    Returns:
        True si rejeté, False si déjà traité
    """
    from orders.models import DeliveryLeg
    
    with transaction.atomic():
        leg_locked = DeliveryLeg.objects.select_for_update().get(pk=leg.pk)
        
        if leg_locked.status not in ("pending",):
            return False
        
        if not leg_locked.offered_at:
            return False
        
        # Réinitialiser l'offre
        leg_locked.offered_at = None
        leg_locked.offer_expires_at = None
        leg_locked.assignment_reason = reason
        leg_locked.save(update_fields=[
            "offered_at",
            "offer_expires_at",
            "assignment_reason",
        ])
        
        return True
