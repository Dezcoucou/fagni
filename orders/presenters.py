from decimal import Decimal
from typing import Any, Dict

from orders.pricing_engine import compute_order_pricing


DECIMAL_ZERO = Decimal("0")


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except Exception:
        return DECIMAL_ZERO


def _safe_items_count(order: Any) -> int:
    try:
        if hasattr(order, "items"):
            return int(order.items.count())
    except Exception:
        import logging
        logging.getLogger("fagni.orders.presenters").exception("Exception silencieuse (auto-log) - fichier=orders/presenters.py ligne=21")
    return 0


def build_order_display_summary(order: Any) -> Dict[str, Any]:
    pricing_mode = (getattr(order, "pricing_mode", None) or "item").lower()
    bag_size = (getattr(order, "bag_size", None) or "").lower()

    bag_label_map = {
        "small": "Petit sac",
        "medium": "Sac moyen",
        "large": "Grand sac",
    }

    is_bag = pricing_mode == "bag"
    is_item = pricing_mode == "item"

    if is_bag and not bag_size:
        bag_size = "medium"

    return {
        "pricing_mode": pricing_mode,
        "is_bag": is_bag,
        "is_item": is_item,
        "pricing_label": "Commande en sac" if is_bag else "Commande à la pièce",
        "bag_size": bag_size,
        "bag_label": bag_label_map.get(bag_size, "Sac moyen") if is_bag else "",
        "content_label": "Vêtements du quotidien" if is_bag else "Articles détaillés",
        "exclusions_label": (
            "Draps, serviettes, couettes, articles volumineux ou spéciaux exclus"
            if is_bag else ""
        ),
        "items_count": 0 if is_bag else _safe_items_count(order),
    }


def build_order_finance_summary(order: Any) -> Dict[str, Any]:
    # CORRECTIF 6 juillet 2026 : l'ancien code appelait compute_order_pricing(order)
    # avec l'objet commande entier (au lieu d'un nombre d'articles), provoquant un
    # TypeError systematique, puis accedait a des attributs (result.xxx) sur un
    # dictionnaire qui ne les contient pas. Consequence : la page de paiement
    # /orders/client/orders/{id}/pay/wave/ plantait en 500 pour tout client.
    #
    # Correctif : lire directement les montants deja verrouilles sur la commande
    # (ADR-001, Immutable Pricing) plutot que de recalculer via le moteur de pricing.
    total_locked = _to_decimal(getattr(order, "total_client_ttc", 0))
    coupon_discount = _to_decimal(getattr(order, "coupon_discount_applied", 0))
    total = total_locked - coupon_discount
    if total < DECIMAL_ZERO:
        total = DECIMAL_ZERO
    paid = _to_decimal(getattr(order, "amount_paid", 0))
    remaining = total - paid
    if remaining < DECIMAL_ZERO:
        remaining = DECIMAL_ZERO

    service_fee = _to_decimal(getattr(order, "service_fee", 0))
    delivery_fee = _to_decimal(getattr(order, "delivery_fee", 0))
    amount_laundry = _to_decimal(getattr(order, "amount_laundry_partner", 0))
    amount_driver = _to_decimal(getattr(order, "amount_driver_partner", 0))
    fagni_revenue = _to_decimal(getattr(order, "fagni_revenue_ht", 0))

    return {
        "prestation_total": total - delivery_fee - service_fee,
        "delivery_fee_client": delivery_fee,
        "delivery_cost_driver": amount_driver,
        "service_fee_ht": service_fee,
        "vat_fagni": DECIMAL_ZERO,
        "service_fee_client_ttc": service_fee,
        "express_surcharge": DECIMAL_ZERO,
        "express_for_client": DECIMAL_ZERO,
        "express_extra_fee_client": DECIMAL_ZERO,
        "commission_laundry_ht": DECIMAL_ZERO,
        "commission_delivery_ht": DECIMAL_ZERO,
        "amount_laundry": amount_laundry,
        "amount_driver": amount_driver,
        "margin_delivery": DECIMAL_ZERO,
        "fagni_revenue_ht": fagni_revenue,
        "upsell_total": DECIMAL_ZERO,
        "total_client_ttc": total,
        "total_before_discount": total_locked,
        "coupon_discount": coupon_discount,
        "coupon_code": getattr(order, "coupon_code", None),
        "amount_paid": paid,
        "amount_remaining": remaining,
    }
