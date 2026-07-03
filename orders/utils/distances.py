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


def osrm_distance_km(origin_lat, origin_lng, dest_lat, dest_lng):
    """
    Distance routiere reelle via OSRM public.
    Fallback sur Haversine si OSRM indisponible.
    Retourne Decimal en km.
    """
    import urllib.request
    import json
    from decimal import Decimal, ROUND_HALF_UP

    try:
        url = (
            f"http://router.project-osrm.org/route/v1/driving/"
            f"{origin_lng},{origin_lat};{dest_lng},{dest_lat}"
            f"?overview=false"
        )
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read())
        if data.get("code") == "Ok":
            meters = data["routes"][0]["legs"][0]["distance"]
            km = meters / 1000
            return Decimal(str(km)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        import logging
        logging.getLogger("fagni.orders.utils.distances").exception("Exception silencieuse (auto-log) - fichier=orders/utils/distances.py ligne=56")
    # Fallback Haversine
    return haversine_distance_km(origin_lat, origin_lng, dest_lat, dest_lng)
