# orders/finance.py

from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any

from .utils.pricing import compute_order_amounts
from .utils.settings_loader import get_pricing_settings


def compute_order_financials(order) -> Dict[str, Any]:
    """
    Point d'entrée unique pour calculer :
      - prestations
      - frais livraisons
      - service FAGNI
      - express
      - commissions blanchisserie / livreur
      - marge logistique
      - TVA
      - total TTC client
    À partir :
      * de la commande (order)
      * de GlobalPricingSettings (admin)
    """
    cfg = get_pricing_settings()
    base = compute_order_amounts(order)

    prestation_total = base["subtotal"]
    delivery_fee_client = base["delivery_fee_client"]
    delivery_cost_driver = base["delivery_cost_driver"]
    service_fee_ht = base["service_fee_ht"]
    express_surcharge = base["express_surcharge"]
    express_for_client = base["express_for_client"]
    commission_laundry_ht = base["commission_laundry_ht"]
    commission_delivery_ht = base["commission_delivery_ht"]
    margin_delivery = base["margin_delivery"]
    fagni_revenue_ht = base["fagni_revenue_ht"]

    # TVA FAGNI : sur le revenu FAGNI uniquement (par défaut)
    vat_base = fagni_revenue_ht
    if not cfg.apply_vat_on_service_only:
        # Extension future : on pourrait ajouter d'autres composantes si besoin
        vat_base = fagni_revenue_ht

    vat_fagni = (
        vat_base * cfg.vat_rate / Decimal("100")
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    total_client_ttc = (
        prestation_total
        + delivery_fee_client
        + service_fee_ht
        + express_for_client
        + vat_fagni
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    return {
        "prestation_total": prestation_total,
        "delivery_fee_client": delivery_fee_client,
        "delivery_cost_driver": delivery_cost_driver,
        "service_fee_ht": service_fee_ht,
        "express_surcharge": express_surcharge,
        "commission_laundry_ht": commission_laundry_ht,
        "commission_delivery_ht": commission_delivery_ht,
        "margin_delivery": margin_delivery,
        "fagni_revenue_ht": fagni_revenue_ht,
        "vat_fagni": vat_fagni,
        "total_ttc_client": total_client_ttc,
    }
