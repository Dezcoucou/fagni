from decimal import Decimal, ROUND_HALF_UP
import math

def haversine_distance_km(origin_lat, origin_lng, dest_lat, dest_lng):
    """
    Distance géodésique (km) entre deux points lat/lng.
    Retourne Decimal(0.01) ou None si données invalides.
    """
    try:
        if origin_lat is None or origin_lng is None or dest_lat is None or dest_lng is None:
            return None
        lat1 = float(origin_lat)
        lon1 = float(origin_lng)
        lat2 = float(dest_lat)
        lon2 = float(dest_lng)
    except (TypeError, ValueError):
        return None

    # Rayon Terre (km)
    R = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (math.sin(dphi / 2) ** 2) + math.cos(phi1) * math.cos(phi2) * (math.sin(dlambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    km = R * c

    return Decimal(str(km)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
