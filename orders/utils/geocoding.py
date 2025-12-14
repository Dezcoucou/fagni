import json
import time
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from django.conf import settings


def _ua() -> str:
    # Nominatim exige un User-Agent identifiable
    return getattr(settings, "NOMINATIM_USER_AGENT", "FAGNI/1.0 (contact: admin@fagni.local)")


def geocode_address_nominatim(address: str, country_codes: str = "ci") -> Optional[Tuple[float, float]]:
    """
    Geocode via OpenStreetMap Nominatim.
    - Ne PAS spammer : limite 1 req/sec (on met un mini sleep en fin)
    """
    if not address:
        return None

    base_url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    }
    if country_codes:
        params["countrycodes"] = country_codes

    url = base_url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _ua(),
            "Accept-Language": "fr",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw) if raw else []
    except Exception:
        return None
    finally:
        # Politesse Nominatim (rate limit)
        time.sleep(1.1)

    if not data:
        return None

    try:
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        return (lat, lon)
    except Exception:
        return None


def ensure_order_geocoded(order, save: bool = True) -> bool:
    """
    Remplit pickup/delivery coords si manquants, sans écraser si déjà présents.
    Retourne True si au moins un champ a été rempli.
    """
    changed = False

    # --- pickup ---
    pickup_lat = getattr(order, "pickup_latitude", None) or getattr(order, "pickup_lat", None)
    pickup_lng = getattr(order, "pickup_longitude", None) or getattr(order, "pickup_lng", None)

    if not pickup_lat or not pickup_lng:
        pickup_addr = getattr(order, "pickup_address", None) or getattr(order.customer, "address", None) or ""
        coords = geocode_address_nominatim(pickup_addr, country_codes=getattr(settings, "GEOCODING_COUNTRY_CODES", "ci"))
        if coords:
            if hasattr(order, "pickup_latitude"):
                order.pickup_latitude = coords[0]
                order.pickup_longitude = coords[1]
            else:
                order.pickup_lat = coords[0]
                order.pickup_lng = coords[1]
            changed = True

    # --- delivery ---
    delivery_lat = getattr(order, "delivery_latitude", None) or getattr(order, "delivery_lat", None)
    delivery_lng = getattr(order, "delivery_longitude", None) or getattr(order, "delivery_lng", None)

    if not delivery_lat or not delivery_lng:
        delivery_addr = getattr(order, "delivery_address", None) or getattr(order, "pickup_address", None) or getattr(order.customer, "address", None) or ""
        coords = geocode_address_nominatim(delivery_addr, country_codes=getattr(settings, "GEOCODING_COUNTRY_CODES", "ci"))
        if coords:
            if hasattr(order, "delivery_latitude"):
                order.delivery_latitude = coords[0]
                order.delivery_longitude = coords[1]
            else:
                order.delivery_lat = coords[0]
                order.delivery_lng = coords[1]
            changed = True

    # --- prestataire (optionnel : on ne modifie pas le partner si déjà GPS) ---
    partner = getattr(order, "laundry_partner", None)
    if partner and (not getattr(partner, "latitude", None) or not getattr(partner, "longitude", None)):
        partner_addr = getattr(partner, "address", None) or getattr(partner, "name", None) or ""
        coords = geocode_address_nominatim(partner_addr, country_codes=getattr(settings, "GEOCODING_COUNTRY_CODES", "ci"))
        if coords:
            try:
                partner.latitude = coords[0]
                partner.longitude = coords[1]
                partner.save(update_fields=["latitude", "longitude"])
            except Exception:
                pass

    if changed and save:
        try:
            order.save(update_fields=[
                "pickup_latitude" if hasattr(order, "pickup_latitude") else "pickup_lat",
                "pickup_longitude" if hasattr(order, "pickup_longitude") else "pickup_lng",
                "delivery_latitude" if hasattr(order, "delivery_latitude") else "delivery_lat",
                "delivery_longitude" if hasattr(order, "delivery_longitude") else "delivery_lng",
            ])
        except Exception:
            # fallback : save simple
            order.save()

    return changed
