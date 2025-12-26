from typing import Optional

from partners.models import DeliveryPartner, LaundryPartner


def assign_best_driver(order=None) -> Optional[DeliveryPartner]:
    """
    Retourne un livreur actif, sinon None.
    (Version minimale pour passer les tests)
    """
    qs = DeliveryPartner.objects.filter(is_active=True)
    if not qs.exists():
        return None
    return qs.order_by("id").first()


def assign_best_laundry(order=None) -> Optional[LaundryPartner]:
    """
    Retourne une blanchisserie active, sinon None.
    (Version minimale pour passer les tests)
    """
    qs = LaundryPartner.objects.filter(is_active=True)
    if not qs.exists():
        return None
    return qs.order_by("id").first()
