from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import requests
from django.conf import settings
from django.utils import timezone


@dataclass
class DeliverySegment:
    distance_km: float
    duration_min: float


@dataclass
class DeliveryQuote:
    s1: DeliverySegment
    s2: DeliverySegment
    s3: DeliverySegment
    total_distance_km: float
    total_duration_min: float
    fuel_cost: int
    driver_cost: int
    client_fee: int
    logistic_margin: int


def _km(value_m: int) -> float:
    """Convertit les mètres en km."""
    return float(value_m) / 1000.0


def _minutes(value_s: int) -> float:
    """Convertit les secondes en minutes."""
    return float(value_s) / 60.0


def _compute_fuel_cost(distance_km: float, duration_min: float) -> int:
    """
    Coût carburant = conso distance + conso embouteillage, au prix du litre.
    """
    cfg = settings.FAGNI_LOGISTICS
    fuel_price = cfg["fuel_price"]
    cons_per_100 = cfg["consumption_l_per_100km"]
    idle_per_h = cfg["idle_consumption_l_per_hour"]

    # Distance : L = (km * L/100km)
    dist_l = distance_km * cons_per_100 / 100.0

    # Embouteillages : L = (minutes / 60) * L/h
    idle_l = (duration_min / 60.0) * idle_per_h

    total_l = dist_l + idle_l
    return int(round(total_l * fuel_price))


def _get_segment_from_google(origin: str, destination: str) -> DeliverySegment:
    """
    Appel Google Distance Matrix pour un segment.
    origin / destination = adresse texte (ou 'lat,lng').
    """
    api_key = getattr(settings, "GOOGLE_DISTANCE_MATRIX_API_KEY", "")
    if not api_key:
        # En prod, tu peux lever une exception ou fallback sur un algo simple
        raise RuntimeError("Aucune clé GOOGLE_DISTANCE_MATRIX_API_KEY configurée.")

    params = {
        "origins": origin,
        "destinations": destination,
        "mode": "driving",
        "departure_time": "now",
        "traffic_model": "best_guess",
        "key": api_key,
    }
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/distancematrix/json",
        params=params,
        timeout=5,
    )
    data = resp.json()
    status = data["rows"][0]["elements"][0]["status"]
    if status != "OK":
        raise RuntimeError(f"Erreur DistanceMatrix : {status}")

    el = data["rows"][0]["elements"][0]
    distance_km = _km(el["distance"]["value"])

    # On prend la durée avec trafic si dispo, sinon la durée normale
    if "duration_in_traffic" in el:
        duration_min = _minutes(el["duration_in_traffic"]["value"])
    else:
        duration_min = _minutes(el["duration"]["value"])

    return DeliverySegment(distance_km=distance_km, duration_min=duration_min)


def compute_delivery_quote_for_order(order) -> DeliveryQuote | None:
    """
    Calcule un devis logistique complet pour une commande FAGNI :
    - S1 : base livreur -> client
    - S2 : client -> blanchisserie
    - S3 : blanchisserie -> client

    ⚠ Nécessite :
      - order.customer.address
      - order.laundry_partner.address (sinon retourne None)
      - une 'base' livreur (pour l’instant on prend la blanchisserie comme base)
    """
    if not order.customer or not order.customer.address:
        return None

    if not order.laundry_partner or not getattr(order.laundry_partner, "address", None):
        # Sans adresse de blanchisserie on ne peut pas faire S2 & S3
        return None

    cfg = settings.FAGNI_LOGISTICS

    client_addr = f"{order.customer.address}, Côte d'Ivoire"
    laundry_addr = f"{order.laundry_partner.address}, Côte d'Ivoire"

    # TODO : quand tu auras une adresse ou lat/lng du livreur,
    # remplace 'base_addr' par order.delivery_partner.base_address ou équivalent.
    base_addr = laundry_addr  # version 1 : on suppose que le livreur part de la blanchisserie

    # --- Appels Google Distance Matrix ---
    # S1 : base livreur -> client
    s1 = _get_segment_from_google(base_addr, client_addr)

    # S2 : client -> blanchisserie
    s2 = _get_segment_from_google(client_addr, laundry_addr)

    # S3 : blanchisserie -> client
    s3 = _get_segment_from_google(laundry_addr, client_addr)

    total_distance = s1.distance_km + s2.distance_km + s3.distance_km
    total_duration = s1.duration_min + s2.duration_min + s3.duration_min

    # --- Coût carburant réel (3 segments) ---
    fuel_cost = _compute_fuel_cost(total_distance, total_duration)

    # --- Usure + temps livreur ---
    wear_cost = int(round(total_distance * cfg["wear_cost_per_km"]))
    time_cost = int(round(total_duration * cfg["time_cost_per_minute"]))

    driver_cost = fuel_cost + wear_cost + time_cost + cfg["driver_fixed_fee"]

    # --- Prix client : il paye S2 + S3 uniquement ---
    client_distance = s2.distance_km + s3.distance_km
    client_fee_raw = cfg["client_fixed_fee"] + int(
        round(client_distance * cfg["client_price_per_km"])
    )

    client_fee = max(cfg["client_min_fee"], min(cfg["client_max_fee"], client_fee_raw))

    # --- Marge logistique FAGNI ---
    logistic_margin = client_fee - driver_cost

    return DeliveryQuote(
        s1=s1,
        s2=s2,
        s3=s3,
        total_distance_km=total_distance,
        total_duration_min=total_duration,
        fuel_cost=fuel_cost,
        driver_cost=driver_cost,
        client_fee=client_fee,
        logistic_margin=logistic_margin,
    )
