from decimal import Decimal
import math

from partners.models import LaundryPartner, DeliveryPartner


def haversine_km(lat1, lon1, lat2, lon2):
    """
    Distance géodésique en km (float) pour l'assignation automatique.
    """
    try:
        if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
            return None

        lat1 = float(lat1)
        lon1 = float(lon1)
        lat2 = float(lat2)
        lon2 = float(lon2)
    except (TypeError, ValueError):
        return None

    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))

    return R * c


def auto_assign_laundry(order):
    """
    Assigne automatiquement la blanchisserie la plus proche du client
    (parmi les partenaires actifs avec coordonnées).
    """
    customer = order.customer
    if not customer:
        return None

    clat = getattr(customer, "latitude", None)
    clng = getattr(customer, "longitude", None)
    if clat is None or clng is None:
        return None

    candidates = LaundryPartner.objects.filter(
        is_active=True,
        latitude__isnull=False,
        longitude__isnull=False,
    )

    best_partner = None
    best_distance = None

    for lp in candidates:
        d = haversine_km(clat, clng, lp.latitude, lp.longitude)
        if d is None:
            continue
        if best_distance is None or d < best_distance:
            best_distance = d
            best_partner = lp

    if best_partner:
        order.laundry_partner = best_partner
        order.save(update_fields=["laundry_partner"])
    return order.laundry_partner


def auto_assign_delivery(order):
    """
    Assigne un livreur par défaut.
    (Simple : premier livreur actif, tu pourras raffiner plus tard :
     par zone, proximité, charge, etc.)
    """
    if order.delivery_partner:
        return order.delivery_partner

    try:
        partner = DeliveryPartner.objects.filter(is_active=True).first()
    except Exception:
        partner = None

    if partner:
        order.delivery_partner = partner
        order.save(update_fields=["delivery_partner"])

    return order.delivery_partner
