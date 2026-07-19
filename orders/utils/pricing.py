# orders/utils/pricing.py
"""
Logique de pricing FAGNI (v2 full paramétrable).

Objectif :
- Centraliser TOUS les calculs financiers à partir :
  * de la commande (order)
  * des paramètres admin (GlobalPricingSettings)
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any

from .settings_loader import get_pricing_settings


BAG_PRICING = {
    "small": {
        "label": "Petit sac",
        "price": Decimal("7000"),
        "estimated_items": 15,
        "max_weight_kg": 6.0,
    },
    "medium": {
        "label": "Sac moyen",
        "price": Decimal("10000"),
        "estimated_items": 25,
        "max_weight_kg": 10.0,
    },
    "large": {
        "label": "Grand sac",
        "price": Decimal("14000"),
        "estimated_items": 40,
        "max_weight_kg": 18.0,
    },
}


def _to_decimal(value) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _q1(amount: Decimal) -> Decimal:
    """Arrondi à 1 FCFA (unité) de manière cohérente partout."""
    try:
        return _to_decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0")


def _round_step(amount: Decimal, step: int) -> Decimal:
    """
    Arrondi au multiple de `step` le plus proche (ex : 25 FCFA).
    step = 0 => pas d'arrondi.
    """
    if step is None or step <= 0:
        return _q1(amount)

    step_dec = Decimal(step)
    quotient = (amount / step_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (quotient * step_dec).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _get_item_total(item) -> Decimal:
    """
    Récupère le total d'une ligne (item).
    Priorité :
    - propriété .total si elle existe
    - sinon unit_price * quantity
    """
    if hasattr(item, "total") and item.total is not None:
        try:
            return _to_decimal(item.total)
        except Exception:
            import logging
            logging.getLogger("fagni.orders.utils.pricing").exception("Exception silencieuse (auto-log) - fichier=orders/utils/pricing.py ligne=77")

    unit = getattr(item, "unit_price", None) or 0
    qty = getattr(item, "quantity", None) or 0
    try:
        return _to_decimal(unit) * _to_decimal(qty)
    except Exception:
        return Decimal("0")


def compute_order_amounts(order) -> Dict[str, Any]:
    """
    Calcule tous les montants FAGNI à partir de l'Order + des paramètres admin.

    Retourne un dict avec les clés :
      - subtotal
      - delivery_fee_client
      - delivery_cost_driver
      - service_fee_ht
      - express_surcharge
      - express_for_client
      - commission_laundry_ht
      - commission_delivery_ht
      - margin_delivery
      - fagni_revenue_ht
    """
    cfg = get_pricing_settings()

    # -------------------------------------------------------
    # 1) Sous-total prestations (HT)
    #    Support hybride :
    #    - pricing_mode="bag"  -> valeur forfaitaire sac
    #    - pricing_mode="item" -> somme des lignes
    # -------------------------------------------------------
    pricing_mode = (getattr(order, "pricing_mode", None) or "bag").strip().lower()

    if pricing_mode == "bag":
        bag_size = (getattr(order, "bag_size", None) or "medium").strip().lower()
        bag_cfg = BAG_PRICING.get(bag_size, BAG_PRICING["medium"])
        subtotal = _to_decimal(bag_cfg.get("price", 0))
    else:
        items_manager = getattr(order, "items", None)
        if items_manager is None or order.pk is None:
            # order.pk is None : instance pas encore inseree en base (ex:
            # appelee depuis Order.save() avant l'INSERT initial, meme
            # cause que recompute_order_distance_from_legs) - la relation
            # items n'est pas encore interrogeable, evite le ValueError.
            subtotal = Decimal("0")
        else:
            items = list(items_manager.all())
            subtotal = sum((_get_item_total(it) for it in items), Decimal("0"))

    subtotal = _q1(subtotal)

    # -------------------------------------------------------
    # 2) Distance & frais de livraison (côté client)
    # -------------------------------------------------------
    raw_total = getattr(order, "distance_km_total", None)
    raw_legacy = getattr(order, "distance_km", None)

    distance_km = _to_decimal(raw_total or 0)

    # fallback legacy ONLY if plausible
    if distance_km <= 0:
        legacy = _to_decimal(raw_legacy or 0)
        # garde-fou : une course pressing en CI ne doit pas être à 885 km 🙂
        if Decimal("0") < legacy <= Decimal("100"):
            distance_km = legacy
        else:
            distance_km = Decimal("0")

    delivery_fee_raw = _to_decimal(cfg.delivery_fixed_fee) + (
        _to_decimal(cfg.delivery_price_per_km) * distance_km
    )

    if subtotal > 0 and delivery_fee_raw < _to_decimal(cfg.delivery_min_fee):
        delivery_fee_raw = _to_decimal(cfg.delivery_min_fee)

    delivery_fee_raw = _q1(delivery_fee_raw)
    delivery_fee_client = delivery_fee_raw

    # -------------------------------------------------------
    # 3) Supplément express
    # -------------------------------------------------------
    express_surcharge = Decimal("0")
    if getattr(order, "delivery_mode", "") == "express":
        percent_part = Decimal("0")
        if _to_decimal(cfg.express_extra_percent) > 0 and subtotal > 0:
            percent_part = subtotal * _to_decimal(cfg.express_extra_percent) / Decimal("100")
        express_surcharge = _to_decimal(cfg.express_extra_fixed) + percent_part

    express_surcharge = _q1(express_surcharge)

    express_for_client = express_surcharge if cfg.express_affects_customer_price else Decimal("0")
    express_for_client = _q1(express_for_client)

    # -------------------------------------------------------
    # 4) Répartition livraison : livreur / FAGNI
    #    (sur la base livraison standard, hors supplément express)
    # -------------------------------------------------------
    driver_amount = (_to_decimal(cfg.driver_share_ratio) / Decimal("100")) * delivery_fee_raw

    if driver_amount < _to_decimal(cfg.driver_min_fee) and delivery_fee_raw > 0:
        driver_amount = _to_decimal(cfg.driver_min_fee)

    driver_amount = _q1(driver_amount)

    # ✅ GARDE-FOU SOURCE-OF-TRUTH :
    # Si le minimum livreur dépasse la livraison client, on remonte
    # la livraison client au minimum nécessaire (sinon marge négative).
    if driver_amount > delivery_fee_raw:
        delivery_fee_raw = driver_amount
        delivery_fee_client = delivery_fee_raw

    # Marge logistique de FAGNI sur la livraison (hors express)
    logistic_margin = delivery_fee_raw - driver_amount
    if logistic_margin < 0:
        logistic_margin = Decimal("0")
    logistic_margin = _q1(logistic_margin)

    # -------------------------------------------------------
    # 5) Blanchisserie : base partenaire, commission FAGNI, net partenaire
    # -------------------------------------------------------
    laundry_base = _q1(subtotal)

    laundry_commission_rate = _to_decimal(getattr(cfg, "laundry_commission_percent", 0))
    if laundry_commission_rate < 0:
        laundry_commission_rate = Decimal("0")
    if laundry_commission_rate > Decimal("100"):
        laundry_commission_rate = Decimal("100")

    laundry_commission_ht = _q1(laundry_base * laundry_commission_rate / Decimal("100"))
    amount_laundry_partner = _q1(laundry_base - laundry_commission_ht)

    # -------------------------------------------------------
    # Routage du supplément express
    # NOTE:
    # - la part express blanchisserie n'est PAS incluse ici;
    #   elle sera gérée plus finement dans finance.py selon SLA.
    # - si l'express cible DRIVER/FAGNI/SPLIT, on ajuste ici la logistique.
    # -------------------------------------------------------
    target = (cfg.express_extra_target or "LAUNDRY").upper()
    if express_surcharge > 0:
        if target == "DRIVER":
            driver_amount += express_surcharge
        elif target == "FAGNI":
            logistic_margin += express_surcharge
        elif target == "SPLIT":
            half = (express_surcharge / Decimal("2")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            driver_amount += half
            logistic_margin += express_surcharge - half
        elif target == "LAUNDRY":
            # géré dans finance.py via express_laundry_share
            pass

    amount_laundry_partner = _q1(amount_laundry_partner)
    laundry_commission_ht = _q1(laundry_commission_ht)
    driver_amount = _q1(driver_amount)
    logistic_margin = _q1(logistic_margin)

    # -------------------------------------------------------
    # 6) Service FAGNI HT (% + minimum + arrondi + option livraison)
    # -------------------------------------------------------
    service_base = subtotal
    if cfg.apply_service_on_delivery:
        service_base += delivery_fee_client

    raw_service = service_base * _to_decimal(cfg.service_fee_percent) / Decimal("100")

    if raw_service < _to_decimal(cfg.service_fee_min) and service_base > 0:
        raw_service = _to_decimal(cfg.service_fee_min)

    service_fee_ht = _round_step(raw_service, cfg.rounding_step)

    # -------------------------------------------------------
    # 7) Revenu FAGNI HT
    # -------------------------------------------------------
    fagni_revenue_ht = _q1(laundry_commission_ht + logistic_margin + service_fee_ht)

    return {
        "subtotal": subtotal,
        "delivery_fee_client": delivery_fee_client,
        "delivery_cost_driver": driver_amount,
        "service_fee_ht": service_fee_ht,
        "express_surcharge": express_surcharge,
        "express_for_client": express_for_client,

        # Blanchisserie
        "laundry_base_ht": laundry_base,
        "commission_laundry_ht": laundry_commission_ht,
        "amount_laundry_partner": amount_laundry_partner,

        # Livraison
        "commission_delivery_ht": driver_amount,   # legacy/compat
        "amount_driver_partner": driver_amount,
        "margin_delivery": logistic_margin,

        # FAGNI
        "fagni_revenue_ht": fagni_revenue_ht,
    }
