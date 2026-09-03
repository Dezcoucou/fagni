"""
Moteur de matching Crowd (Cotransportage).

Architecture :
- pick_best_cotransporter(leg) : fonction principale
- Calcule un score déterministe basé sur :
  * Compatibilité géographique (origine/destination)
  * Détour max acceptable
  * Fenêtre horaire
  * Score de fiabilité du cotransporteur
  * Capacité
- Retourne (cotransporter, reason) ou (None, reason)
- Ne lève jamais d'exception

Reasons standardisés :
- NO_MATCH : aucun cotransporteur compatible trouvé
- NO_AVAILABLE : aucun cotransporteur actif/vérifié
- DETOUR_TOO_HIGH : détour requis dépasse max_detour_km
- TIME_INCOMPATIBLE : créneau horaire incompatible
- CAPACITY : capacité insuffisante
- UNVERIFIED : cotransporteur non vérifié
- INACTIVE : cotransporteur inactif
- CONFLICT : conflit de mission (déjà assigné sur ce créneau)
"""
from decimal import Decimal
from typing import Optional, Tuple
from datetime import datetime, timedelta
import math

from django.utils import timezone
from django.db.models import Q

from crowd.models import Cotransporter, CotransporterRoute, CrowdSettings


def _haversine_km(lat1, lng1, lat2, lng2) -> Optional[float]:
    """
    Calcule la distance entre deux points GPS en km (formule de Haversine).
    Retourne None si les coordonnées sont invalides.
    """
    try:
        lat1_f = float(lat1)
        lng1_f = float(lng1)
        lat2_f = float(lat2)
        lng2_f = float(lng2)
    except (TypeError, ValueError):
        return None

    if lat1_f == 0 or lng1_f == 0 or lat2_f == 0 or lng2_f == 0:
        return None

    R = 6371  # Rayon de la Terre en km

    dlat = math.radians(lat2_f - lat1_f)
    dlng = math.radians(lng2_f - lng1_f)

    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1_f)) * math.cos(math.radians(lat2_f)) *
         math.sin(dlng / 2) ** 2)
    c = 2 * math.asin(math.sqrt(a))

    return R * c


def _calculate_detour(
    route_origin_lat, route_origin_lng,
    route_dest_lat, route_dest_lng,
    leg_pickup_lat, leg_pickup_lng,
    leg_delivery_lat, leg_delivery_lng
) -> Optional[float]:
    """
    Calcule le détour nécessaire pour qu'un cotransporteur effectue une jambe.

    Détour = (distance origine_route → pickup) + (distance delivery → destination_route)

    Retourne None si les coordonnées sont invalides.
    """
    dist_origin_to_pickup = _haversine_km(
        route_origin_lat, route_origin_lng,
        leg_pickup_lat, leg_pickup_lng
    )

    dist_delivery_to_dest = _haversine_km(
        leg_delivery_lat, leg_delivery_lng,
        route_dest_lat, route_dest_lng
    )

    if dist_origin_to_pickup is None or dist_delivery_to_dest is None:
        return None

    return dist_origin_to_pickup + dist_delivery_to_dest


def _is_time_compatible(
    route_departure_time,
    route_time_window_minutes,
    leg_scheduled_pickup_at
) -> bool:
    """
    Vérifie si l'heure de départ du cotransporteur est compatible
    avec l'heure de ramassage prévue pour la jambe.

    Compatible si : |departure_time - scheduled_pickup| <= time_window
    """
    if not route_departure_time or not leg_scheduled_pickup_at:
        return False

    # Convertir en minutes depuis minuit pour comparaison
    route_minutes = route_departure_time.hour * 60 + route_departure_time.minute

    # leg_scheduled_pickup_at est un datetime
    leg_minutes = leg_scheduled_pickup_at.hour * 60 + leg_scheduled_pickup_at.minute

    diff = abs(route_minutes - leg_minutes)

    # Gérer le cas où la différence traverse minuit
    if diff > 720:  # plus de 12h
        diff = 1440 - diff

    return diff <= route_time_window_minutes


def _get_leg_scheduled_pickup_at(leg):
    """
    Retourne le datetime prévu pour la collecte d'une DeliveryLeg.

    Order ne possède pas scheduled_pickup_at.
    La source de vérité est :
      - pickup_scheduled_date
      - pickup_scheduled_time

    Retourne None si l'un des deux éléments est absent.
    """
    from datetime import datetime

    order = getattr(leg, "order", None)
    if order is None:
        return None

    pickup_date = getattr(order, "pickup_scheduled_date", None)
    pickup_time = getattr(order, "pickup_scheduled_time", None)

    if not pickup_date or not pickup_time:
        return None

    return datetime.combine(pickup_date, pickup_time)


def _has_conflict(cotransporter, leg) -> bool:
    """
    Vérifie si le cotransporteur a déjà une mission sur le même créneau.

    Conflit si une autre DeliveryLeg :
      - utilise le même cotransporteur ;
      - est active (assigned / in_progress) ;
      - possède une collecte prévue dans une fenêtre de +/- 2 heures.

    La date/heure de collecte provient de Order :
      pickup_scheduled_date + pickup_scheduled_time.
    """
    from datetime import timedelta
    from orders.models import DeliveryLeg

    scheduled_pickup_at = _get_leg_scheduled_pickup_at(leg)

    # Pas d'horaire prévu => impossible de déterminer un conflit temporel.
    if not scheduled_pickup_at:
        return False

    window_start = scheduled_pickup_at - timedelta(hours=2)
    window_end = scheduled_pickup_at + timedelta(hours=2)

    conflicting_legs = DeliveryLeg.objects.filter(
        actor_type="cotransporter",
        cotransporter=cotransporter,
        status__in=["assigned", "in_progress"],
        order__pickup_scheduled_date__isnull=False,
        order__pickup_scheduled_time__isnull=False,
    ).exclude(id=leg.id)

    for other_leg in conflicting_legs:
        other_scheduled = _get_leg_scheduled_pickup_at(other_leg)

        if other_scheduled and window_start <= other_scheduled <= window_end:
            return True

    return False


def pick_best_cotransporter(leg) -> Tuple[Optional[Cotransporter], str]:
    """
    Trouve le meilleur cotransporteur compatible pour une DeliveryLeg.

    Algorithme :
    1. Vérifier que le module Crowd est activé
    2. Récupérer les coordonnées de la jambe (pickup/delivery)
    3. Trouver tous les cotransporteurs actifs et vérifiés
    4. Pour chaque cotransporteur, vérifier ses routes actives
    5. Calculer le détour et vérifier la compatibilité horaire
    6. Calculer un score déterministe
    7. Retourner le meilleur candidat ou (None, reason)

    Args:
        leg: DeliveryLeg pour laquelle trouver un cotransporteur

    Returns:
        Tuple (cotransporter, reason) où :
        - cotransporter : Cotransporter ou None
        - reason : code standardisé (NO_MATCH, DETOUR_TOO_HIGH, etc.)
    """
    # 1. Vérifier que le module Crowd est activé
    try:
        settings = CrowdSettings.get_solo()
        if not settings.crowd_enabled:
            return None, "CROWD_DISABLED"
    except Exception:
        return None, "CROWD_DISABLED"

    # 2. Récupérer les coordonnées de la jambe
    order = leg.order

    # Coordonnées pickup (client → pressing)
    # Order a pickup_lat/pickup_lng (FloatField), fallback sur customer.latitude/longitude
    pickup_lat = getattr(order, 'pickup_lat', None)
    pickup_lng = getattr(order, 'pickup_lng', None)
    if (not pickup_lat or not pickup_lng) and order.customer:
        pickup_lat = getattr(order.customer, 'latitude', None)
        pickup_lng = getattr(order.customer, 'longitude', None)

    # Coordonnées delivery (pressing → client)
    delivery_lat = getattr(order, 'delivery_lat', None)
    delivery_lng = getattr(order, 'delivery_lng', None)

    if not pickup_lat or not pickup_lng:
        return None, "NO_COORDINATES"

    # Pour une jambe pickup : origine = client, destination = pressing
    # Pour une jambe return : origine = pressing, destination = client
    if leg.leg_type == 'pickup':
        leg_origin_lat = pickup_lat
        leg_origin_lng = pickup_lng
        leg_dest_lat = delivery_lat or pickup_lat  # fallback si delivery pas défini
        leg_dest_lng = delivery_lng or pickup_lng
    else:  # return
        leg_origin_lat = delivery_lat or pickup_lat
        leg_origin_lng = delivery_lng or pickup_lng
        leg_dest_lat = pickup_lat
        leg_dest_lng = pickup_lng

    # 3. Trouver tous les cotransporteurs actifs et vérifiés
    cotransporters = Cotransporter.objects.filter(
        is_active=True,
        is_verified=True,
        score__gte=settings.crowd_min_score
    )

    if not cotransporters.exists():
        return None, "NO_AVAILABLE"

    # 4. Pour chaque cotransporteur, évaluer ses routes
    best_candidate = None
    best_score = -1
    rejection_reason = "NO_MATCH"

    for cotransporter in cotransporters:
        # Vérifier la capacité
        if cotransporter.capacity_kg < Decimal('1.0'):  # capacité minimale
            rejection_reason = "CAPACITY"
            continue

        # Vérifier les conflits
        if _has_conflict(cotransporter, leg):
            rejection_reason = "CONFLICT"
            continue

        # Récupérer les routes actives du cotransporteur
        routes = CotransporterRoute.objects.filter(
            cotransporter=cotransporter,
            is_active=True
        )

        if not routes.exists():
            continue

        # Vérifier la date de validité des routes
        today = timezone.now().date()

        for route in routes:
            # Vérifier que la route est valide aujourd'hui
            if not route.is_valid_on_date(today):
                continue

            # 5. Calculer le détour
            detour = _calculate_detour(
                route.origin_lat, route.origin_lng,
                route.destination_lat, route.destination_lng,
                leg_origin_lat, leg_origin_lng,
                leg_dest_lat, leg_dest_lng
            )

            if detour is None:
                continue

            # Vérifier que le détour est acceptable.
            # Le moteur de distance retourne un float ; les paramètres
            # métier sont Decimal. Conversion explicite pour éviter
            # tout mélange implicite de types.
            max_detour = min(
                float(route.max_detour_km),
                float(settings.crowd_max_detour_km),
            )

            if max_detour <= 0:
                rejection_reason = "DETOUR_TOO_HIGH"
                continue

            if detour > max_detour:
                rejection_reason = "DETOUR_TOO_HIGH"
                continue

            # 6. Vérifier la compatibilité horaire
            scheduled_pickup = _get_leg_scheduled_pickup_at(leg)
            if scheduled_pickup:
                if not _is_time_compatible(
                    route.departure_time,
                    route.time_window_minutes,
                    scheduled_pickup
                ):
                    rejection_reason = "TIME_INCOMPATIBLE"
                    continue

            # 7. Calculer un score déterministe.
            #
            # Plus le détour est faible, plus le score géographique est élevé.
            # Le score de fiabilité du cotransporteur pondère ensuite ce résultat.
            detour_ratio = (detour / max_detour) * 100.0
            geographic_score = max(0.0, 100.0 - detour_ratio)
            reliability_factor = max(
                0.0,
                min(1.0, float(cotransporter.score) / 100.0)
            )

            score = geographic_score * reliability_factor

            if score > best_score:
                best_score = score
                best_candidate = cotransporter

    if best_candidate:
        return best_candidate, "MATCHED"

    return None, rejection_reason
