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
    delivery_fee_client = base["delivery_fee_client"]          # ✅ transport facturé au client (hors express)
    delivery_cost_driver = base["delivery_cost_driver"]
    service_fee_ht = base["service_fee_ht"]

    # EXPRESS :
    # - express_surcharge : surcharge technique / interne (si ton moteur la calcule)
    # - express_for_client : montant réellement facturé au client pour l’option express
    express_surcharge = base.get("express_surcharge", Decimal("0"))
    express_for_client = base.get("express_for_client", Decimal("0"))

    commission_laundry_ht = base["commission_laundry_ht"]
    commission_delivery_ht = base["commission_delivery_ht"]
    margin_delivery = base["margin_delivery"]

    # ------------------------------------------------------------
    # GARDE-FOU (double sécurité) :
    # - marge logistique ne peut pas être négative
    # - si coût livreur > prix client livraison, on remonte le prix client
    #   au minimum au coût livreur et on force marge à 0.
    # ------------------------------------------------------------
    if margin_delivery < 0:
        margin_delivery = Decimal("0")

    if delivery_cost_driver > delivery_fee_client:
        delivery_fee_client = delivery_cost_driver
        margin_delivery = Decimal("0")

    # Revenu FAGNI HT (source-of-truth) = marge livraison + service
    fagni_revenue_ht = (margin_delivery + service_fee_ht).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )

    # TVA FAGNI : sur le revenu FAGNI uniquement (par défaut)
    vat_base = fagni_revenue_ht
    if not cfg.apply_vat_on_service_only:
        vat_base = fagni_revenue_ht

    vat_fagni = (vat_base * cfg.vat_rate / Decimal("100")).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )

    # ✅ Total TTC client = prestations + transport + service + express + TVA FAGNI
    total_client_ttc = (
        prestation_total
        + delivery_fee_client
        + service_fee_ht
        + express_for_client
        + vat_fagni
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    return {
        "prestation_total": prestation_total,

        # Livraison (client + driver)
        "delivery_fee_client": delivery_fee_client,
        "delivery_cost_driver": delivery_cost_driver,

        # Service FAGNI
        "service_fee_ht": service_fee_ht,

        # Express (on renvoie les 2 infos)
        "express_surcharge": express_surcharge,              # interne / technique (si utilisé ailleurs)
        "express_for_client": express_for_client,            # ✅ montant facturé au client
        "express_extra_fee_client": express_for_client,      # ✅ alias explicite (optionnel)

        # Commissions + marge
        "commission_laundry_ht": commission_laundry_ht,
        "commission_delivery_ht": commission_delivery_ht,
        "margin_delivery": margin_delivery,

        # Revenu + TVA
        "fagni_revenue_ht": fagni_revenue_ht,
        "vat_fagni": vat_fagni,

        # ✅ clé officielle (à utiliser partout)
        "total_client_ttc": total_client_ttc,

        # ✅ compat legacy
        "total_ttc_client": total_client_ttc,
    }
