# orders/utils/assign.py
"""
Wrappers legacy (compat) vers le nouveau moteur d'assignation orders/assignment.py

Objectif:
- garder la compatibilité avec d'anciens imports: from orders.utils import auto_assign_delivery, ...
- éviter toute logique métier ici (source unique = orders/assignment.py)
"""

from __future__ import annotations

from typing import Optional

from partners.models import LaundryPartner, DeliveryPartner
from orders.assignment import pick_best_laundry, pick_best_driver, _haversine_km


def haversine_km(lat1, lon1, lat2, lon2):
    """Compat: expose la distance haversine en km."""
    return _haversine_km(lat1, lon1, lat2, lon2)


def auto_assign_laundry(order) -> Optional[LaundryPartner]:
    """
    Compat: assigne une blanchisserie à la commande via pick_best_laundry(order)
    et sauvegarde si trouvé.
    """
    lp, _reason = pick_best_laundry(order)
    if lp:
        order.laundry_partner = lp
        order.save(update_fields=["laundry_partner"])
        return lp
    return None


def auto_assign_delivery(order) -> Optional[DeliveryPartner]:
    """
    Compat: assigne un livreur à la commande via pick_best_driver(order)
    et sauvegarde si trouvé.
    """
    dp, reason = pick_best_driver(order)
    if dp:
        order.delivery_partner = dp
        order.delivery_partner_unassigned_reason = None
        order.save(update_fields=["delivery_partner", "delivery_partner_unassigned_reason"])
        return dp

    # si aucun dp, on stocke la raison si le champ existe (utile debug)
    if hasattr(order, "delivery_partner_unassigned_reason"):
        order.delivery_partner_unassigned_reason = reason or "Aucun livreur éligible."
        order.save(update_fields=["delivery_partner_unassigned_reason"])

    return None
