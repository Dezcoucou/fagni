from decimal import Decimal
from typing import Any, Dict

from orders.finance import compute_order_financials


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
        pass
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
    data = compute_order_financials(order)

    prestation_total = _to_decimal(data.get("prestation_total", 0))
    delivery_fee_client = _to_decimal(data.get("delivery_fee_client", 0))
    service_fee_ht = _to_decimal(data.get("service_fee_ht", 0))
    vat_fagni = _to_decimal(data.get("vat_fagni", 0))
    total_client_ttc = _to_decimal(data.get("total_client_ttc", 0))
    amount_paid = _to_decimal(getattr(order, "amount_paid", 0))

    service_fee_client_ttc = service_fee_ht + vat_fagni
    amount_remaining = total_client_ttc - amount_paid
    if amount_remaining < DECIMAL_ZERO:
        amount_remaining = DECIMAL_ZERO

    return {
        "prestation_total": prestation_total,
        "delivery_fee_client": delivery_fee_client,
        "service_fee_ht": service_fee_ht,
        "vat_fagni": vat_fagni,
        "service_fee_client_ttc": service_fee_client_ttc,
        "total_client_ttc": total_client_ttc,
        "amount_paid": amount_paid,
        "amount_remaining": amount_remaining,
    }
