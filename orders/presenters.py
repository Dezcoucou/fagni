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
    result = compute_order_pricing(order)

    return {
        "prestation_total": _to_decimal(result.prestation_total),
        "delivery_fee_client": _to_decimal(result.delivery_fee_client),
        "delivery_cost_driver": _to_decimal(result.delivery_cost_driver),
        "service_fee_ht": _to_decimal(result.service_fee_ht),
        "vat_fagni": _to_decimal(result.vat_fagni),
        "service_fee_client_ttc": _to_decimal(result.service_fee_client_ttc),
        "express_surcharge": _to_decimal(result.express_surcharge),
        "express_for_client": _to_decimal(result.express_for_client),
        "express_extra_fee_client": _to_decimal(result.express_extra_fee_client),
        "commission_laundry_ht": _to_decimal(result.commission_laundry_ht),
        "commission_delivery_ht": _to_decimal(result.commission_delivery_ht),
        "amount_laundry": _to_decimal(result.amount_laundry),
        "amount_driver": _to_decimal(result.amount_driver),
        "margin_delivery": _to_decimal(result.margin_delivery),
        "fagni_revenue_ht": _to_decimal(result.fagni_revenue_ht),
        "upsell_total": _to_decimal(result.upsell_total),
        "total_client_ttc": _to_decimal(result.total_client_ttc),
        "amount_paid": _to_decimal(result.amount_paid),
        "amount_remaining": _to_decimal(result.amount_remaining),
    }
