# orders/assignment.py
from __future__ import annotations

import math
from decimal import Decimal
from typing import Optional, Tuple, Any

from django.db.models import Q

from partners.models import DeliveryPartner, LaundryPartner
from orders.utils.settings_loader import get_assignment_settings


def _haversine_km(lat1, lon1, lat2, lon2) -> Optional[float]:
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

    a = (math.sin(dphi / 2.0) ** 2) + (math.cos(phi1) * math.cos(phi2) * (math.sin(dlambda / 2.0) ** 2))
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def _extract_point_from_args(*args: Any) -> Tuple[Optional[Any], Optional[Decimal], Optional[Decimal]]:
    if not args:
        return None, None, None

    # (lat, lng)
    if len(args) >= 2 and isinstance(args[0], (int, float, Decimal)) and isinstance(args[1], (int, float, Decimal)):
        try:
            return None, Decimal(str(args[0])), Decimal(str(args[1]))
        except Exception:
            return None, None, None

    obj = args[0]

    lat = getattr(obj, "latitude", None)
    lng = getattr(obj, "longitude", None)

    # Order / objet logistique : priorité aux coords de collecte
    if lat is None or lng is None:
        lat = getattr(obj, "pickup_lat", lat)
        lng = getattr(obj, "pickup_lng", lng)

    # Fallback éventuel coords de livraison
    if lat is None or lng is None:
        lat = getattr(obj, "delivery_lat", lat)
        lng = getattr(obj, "delivery_lng", lng)

    # order.customer fallback
    if (lat is None or lng is None) and hasattr(obj, "customer"):
        c = getattr(obj, "customer", None)
        if c is not None:
            lat = getattr(c, "latitude", None)
            lng = getattr(c, "longitude", None)

    try:
        lat = Decimal(str(lat)) if lat is not None else None
        lng = Decimal(str(lng)) if lng is not None else None
    except Exception:
        lat, lng = None, None

    return obj, lat, lng


def _driver_load_count(driver: DeliveryPartner) -> int:
    try:
        from orders.models import Order
        return Order.objects.filter(
            delivery_partner=driver,
            status__in=["pending", "in_progress"],
        ).count()
    except Exception:
        return 0


def assign_best_driver(*args, **kwargs) -> Optional[DeliveryPartner]:
    driver, _reason = pick_best_driver(*args, **kwargs)
    return driver


def pick_best_driver(*args, **kwargs) -> Tuple[Optional[DeliveryPartner], Optional[str]]:
    cfg = get_assignment_settings()

    if cfg.driver_assignment_mode == "manual":
        return None, "Assignation manuelle (auto désactivé)."

    obj, lat, lng = _extract_point_from_args(*args)

    if lat is None or lng is None:
        return None, "Coordonnées client indisponibles (lat/lng manquants)."

    qs = DeliveryPartner.objects.filter(is_active=True)

    # Filtre ville si possible
    city = None
    if obj is not None:
        city = getattr(obj, "city", None)
        if not city and hasattr(obj, "customer"):
            city = getattr(getattr(obj, "customer", None), "city", None)
    if city:
        qs = qs.filter(Q(city__iexact=city) | Q(city__icontains=city))

    # Candidats géolocalisés
    qs = qs.filter(latitude__isnull=False, longitude__isnull=False)

    if not qs.exists():
        fallback = DeliveryPartner.objects.filter(is_active=True).order_by("id").first()
        if fallback:
            return fallback, "Livreur assigné en fallback (GPS manquant sur les partenaires)."
        return None, "Aucun livreur actif disponible."

    radius_km = float(cfg.driver_radius_km or 0)
    best = None
    best_d = None

    for dp in qs:
        d = _haversine_km(lat, lng, dp.latitude, dp.longitude)
        if d is None:
            continue

        if radius_km > 0 and d > radius_km:
            continue

        load = _driver_load_count(dp)
        if cfg.max_concurrent_orders_per_driver and load >= int(cfg.max_concurrent_orders_per_driver):
            continue

        if best_d is None or d < best_d:
            best_d = d
            best = dp

    if best:
        return best, None

    if cfg.driver_assignment_mode == "hybrid":
        fallback = DeliveryPartner.objects.filter(is_active=True).order_by("id").first()
        if fallback:
            return fallback, "Fallback hybrid : livreur actif assigné malgré contraintes (rayon/charge)."

    return None, "Aucun livreur éligible (rayon/charge/ville)."


def assign_best_laundry(*args, **kwargs) -> Optional[LaundryPartner]:
    lp, _reason = pick_best_laundry(*args, **kwargs)
    return lp


def pick_best_laundry(*args, **kwargs) -> Tuple[Optional[LaundryPartner], Optional[str]]:
    cfg = get_assignment_settings()

    obj, lat, lng = _extract_point_from_args(*args)
    qs = LaundryPartner.objects.filter(is_active=True)

    if not qs.exists():
        return None, "Aucune blanchisserie active."

    if lat is None or lng is None:
        return qs.order_by("id").first(), "Blanchisserie assignée en fallback (coords client manquantes)."

    if cfg.laundry_selection_mode == "manual":
        return None, "Sélection blanchisserie manuelle (auto désactivé)."

    if cfg.laundry_selection_mode == "priority":
        return qs.order_by("id").first(), "Mode priorité non implémenté v1 (fallback 1ère active)."

    # closest
    qs = qs.filter(latitude__isnull=False, longitude__isnull=False)
    best = None
    best_d = None
    for lp in qs:
        d = _haversine_km(lat, lng, lp.latitude, lp.longitude)
        if d is None:
            continue
        if best_d is None or d < best_d:
            best_d = d
            best = lp

    if best:
        return best, None

    return LaundryPartner.objects.filter(is_active=True).order_by("id").first(), "Fallback : aucune blanchisserie géolocalisée."
