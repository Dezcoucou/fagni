# orders/distance.py
from decimal import Decimal
import math


def _to_float(value):
    """
    Convertit un Decimal / string / nombre en float.
    Gère aussi les virgules françaises "5,355249" -> 5.355249
    """
    if value is None:
        return None
    s = str(value).strip().replace(",", ".")
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def distance_matrix_one_way_km(origin_lat, origin_lng, dest_lat, dest_lng):
    """
    Calcule la distance "à vol d'oiseau" (Haversine) entre deux points en km.
    On renvoie la distance ALLER SIMPLE (client -> blanchisserie) en Decimal.
    Si un des points est invalide, on renvoie None.
    """

    lat1 = _to_float(origin_lat)
    lon1 = _to_float(origin_lng)
    lat2 = _to_float(dest_lat)
    lon2 = _to_float(dest_lng)

    # Si on n'arrive pas à convertir, on abandonne
    if None in (lat1, lon1, lat2, lon2):
        return None

    # Formule de Haversine
    R = 6371.0  # rayon de la Terre en km

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    km = R * c  # float
    # On renvoie un Decimal avec 3 décimales, suffisant pour ton besoin
    return Decimal(str(round(km, 3)))
