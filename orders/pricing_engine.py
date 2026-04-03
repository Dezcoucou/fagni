from __future__ import annotations

from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Any, Dict

from orders.finance import compute_order_financials


DECIMAL_ZERO = Decimal("0.00")


def _to_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:
        return DECIMAL_ZERO


@dataclass(frozen=True)
class PricingResult:
    prestation_total: Decimal
    delivery_fee_client: Decimal
    delivery_cost_driver: Decimal
    service_fee_ht: Decimal
    vat_fagni: Decimal
    service_fee_client_ttc: Decimal
    express_surcharge: Decimal
    express_for_client: Decimal
    express_extra_fee_client: Decimal
    commission_laundry_ht: Decimal
    commission_delivery_ht: Decimal
    amount_laundry: Decimal
    amount_driver: Decimal
    margin_delivery: Decimal
    fagni_revenue_ht: Decimal
    upsell_total: Decimal
    total_client_ttc: Decimal
    amount_paid: Decimal
    amount_remaining: Decimal

    def to_dict(self) -> Dict[str, Decimal]:
        return asdict(self)


def compute_order_pricing(order) -> PricingResult:
    """
    Source unique de vérité du pricing affichable / payable.
    Ne modifie rien en base.
    """
    raw = compute_order_financials(order) or {}

    prestation_total = _to_decimal(raw.get("prestation_total"))
    delivery_fee_client = _to_decimal(raw.get("delivery_fee_client"))
    delivery_cost_driver = _to_decimal(raw.get("delivery_cost_driver"))
    service_fee_ht = _to_decimal(raw.get("service_fee_ht"))
    vat_fagni = _to_decimal(raw.get("vat_fagni"))
    express_surcharge = _to_decimal(raw.get("express_surcharge"))
    express_for_client = _to_decimal(raw.get("express_for_client"))
    express_extra_fee_client = _to_decimal(raw.get("express_extra_fee_client"))
    commission_laundry_ht = _to_decimal(raw.get("commission_laundry_ht"))
    commission_delivery_ht = _to_decimal(raw.get("commission_delivery_ht"))
    amount_laundry = _to_decimal(raw.get("amount_laundry"))
    amount_driver = _to_decimal(raw.get("amount_driver"))
    margin_delivery = _to_decimal(raw.get("margin_delivery"))
    fagni_revenue_ht = _to_decimal(raw.get("fagni_revenue_ht"))

    service_fee_client_ttc = service_fee_ht + vat_fagni

    try:
        upsell = getattr(order, "upsell", None)
        upsell_total = _to_decimal(getattr(upsell, "total", 0)) if upsell else DECIMAL_ZERO
    except Exception:
        upsell_total = DECIMAL_ZERO

    total_client_ttc = _to_decimal(
        raw.get("total_client_ttc", raw.get("total_ttc_client", 0))
    )

    if total_client_ttc <= DECIMAL_ZERO:
        total_client_ttc = (
            prestation_total
            + delivery_fee_client
            + service_fee_client_ttc
            + express_extra_fee_client
            + upsell_total
        )

    amount_paid = _to_decimal(getattr(order, "amount_paid", 0))
    if amount_paid < DECIMAL_ZERO:
        amount_paid = DECIMAL_ZERO

    amount_remaining = total_client_ttc - amount_paid
    if amount_remaining < DECIMAL_ZERO:
        amount_remaining = DECIMAL_ZERO

    return PricingResult(
        prestation_total=prestation_total,
        delivery_fee_client=delivery_fee_client,
        delivery_cost_driver=delivery_cost_driver,
        service_fee_ht=service_fee_ht,
        vat_fagni=vat_fagni,
        service_fee_client_ttc=service_fee_client_ttc,
        express_surcharge=express_surcharge,
        express_for_client=express_for_client,
        express_extra_fee_client=express_extra_fee_client,
        commission_laundry_ht=commission_laundry_ht,
        commission_delivery_ht=commission_delivery_ht,
        amount_laundry=amount_laundry,
        amount_driver=amount_driver,
        margin_delivery=margin_delivery,
        fagni_revenue_ht=fagni_revenue_ht,
        upsell_total=upsell_total,
        total_client_ttc=total_client_ttc,
        amount_paid=amount_paid,
        amount_remaining=amount_remaining,
    )
