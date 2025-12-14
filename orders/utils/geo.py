# orders/utils/geo.py

from typing import Optional, Tuple

Coord = Optional[Tuple[float, float]]


def _to_float(v):
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _coord(lat, lng) -> Coord:
    lat = _to_float(lat)
    lng = _to_float(lng)
    if lat is None or lng is None:
        return None
    return (lat, lng)


def resolve_pickup_coords(order) -> Coord:
    """
    Retourne (lat, lng) de la collecte.
    ✅ Nouveau schéma: pickup_lat / pickup_lng
    🔁 Fallback ancien schéma: pickup_latitude / pickup_longitude
    🔁 Fallback client: customer.latitude / customer.longitude
    """
    # Nouveau champs (ton modèle)
    c = _coord(getattr(order, "pickup_lat", None), getattr(order, "pickup_lng", None))
    if c:
        return c

    # Ancien noms (si jamais encore présent sur d'autres environnements)
    c = _coord(getattr(order, "pickup_latitude", None), getattr(order, "pickup_longitude", None))
    if c:
        return c

    # Fallback client
    cust = getattr(order, "customer", None)
    if cust:
        c = _coord(getattr(cust, "latitude", None), getattr(cust, "longitude", None))
        if c:
            return c

    return None


def resolve_delivery_coords(order) -> Coord:
    """
    Retourne (lat, lng) de la livraison.
    ✅ Nouveau schéma: delivery_lat / delivery_lng
    🔁 Fallback ancien schéma: delivery_latitude / delivery_longitude
    """
    c = _coord(getattr(order, "delivery_lat", None), getattr(order, "delivery_lng", None))
    if c:
        return c

    c = _coord(getattr(order, "delivery_latitude", None), getattr(order, "delivery_longitude", None))
    if c:
        return c

    return None


def resolve_provider_coords(order) -> Coord:
    """
    Retourne (lat, lng) du prestataire (laundry_partner ou autre).
    On garde latitude/longitude côté partner (comme dans ton template).
    """
    partner = getattr(order, "laundry_partner", None)
    if not partner:
        return None

    return _coord(getattr(partner, "latitude", None), getattr(partner, "longitude", None))
