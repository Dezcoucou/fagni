"""
FAGNI Pricing Engine v1.0
Source of truth unique pour tous les calculs financiers.
NE JAMAIS calculer les marges dans le frontend.
"""
from decimal import Decimal, ROUND_HALF_UP


def d(val):
    """Convertir en Decimal propre."""
    return Decimal(str(val or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


# Taux officiels FAGNI
COMMISSION_PRESSING   = Decimal('0.30')   # 30% sur prestation pressing
SERVICE_FEE_RATE      = Decimal('0.05')   # 5% service fee client
COMMISSION_LIVRAISON  = Decimal('0.30')   # 30% sur frais livraison
PART_LIVREUR          = Decimal('0.70')   # 70% des frais livraison au livreur


def calculate_order(pressing_amount, delivery_fee=2000):
    """
    Calcul complet d'une commande FAGNI.

    Args:
        pressing_amount : Prix de la prestation pressing (FCFA)
        delivery_fee    : Frais de livraison facturés au client (défaut 2000 FCFA)

    Returns:
        dict avec tous les montants décomposés
    """
    pressing   = d(pressing_amount)
    livraison  = d(delivery_fee)

    # Commission pressing
    commission_pressing = (pressing * COMMISSION_PRESSING).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    part_pressing       = pressing - commission_pressing

    # Service fee
    service_fee = (pressing * SERVICE_FEE_RATE).quantize(Decimal('1'), rounding=ROUND_HALF_UP)

    # Livraison
    part_livreur       = (livraison * PART_LIVREUR).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    marge_livraison    = livraison - part_livreur

    # Totaux
    total_client = pressing + livraison + service_fee
    total_fagni  = commission_pressing + marge_livraison + service_fee
    total_ops    = pressing + livraison  # ce que FAGNI encaisse avant redistribution

    return {
        # Ce que le client paie
        'total_client':         int(total_client),
        'pressing_amount':      int(pressing),
        'delivery_fee':         int(livraison),
        'service_fee':          int(service_fee),

        # Ce que FAGNI redistribue
        'part_pressing':        int(part_pressing),
        'part_livreur':         int(part_livreur),

        # Revenus FAGNI
        'commission_pressing':  int(commission_pressing),
        'marge_livraison':      int(marge_livraison),
        'total_fagni':          int(total_fagni),

        # Taux appliqués
        'taux_commission':      float(COMMISSION_PRESSING),
        'taux_service_fee':     float(SERVICE_FEE_RATE),
        'taux_livraison':       float(COMMISSION_LIVRAISON),
    }


def calculate_bag_pricing(bag_size, zone='standard'):
    """
    Prix par défaut selon la taille du sac.
    Modifiable via admin Django plus tard.
    """
    BAG_PRICES = {
        'small':  5000,
        'medium': 8000,
        'large':  12000,
    }
    DELIVERY_FEES = {
        'standard': 2000,
        'express':  3500,
        'premium':  5000,
    }
    pressing_amount = BAG_PRICES.get(bag_size, 5000)
    delivery_fee    = DELIVERY_FEES.get(zone, 2000)
    return calculate_order(pressing_amount, delivery_fee)


def calculate_mlm_commissions(fagni_revenue, levels=None):
    """
    Calcul des commissions MLM sur le service fee FAGNI.
    levels: [0.30, 0.15, 0.05] = N1 30%, N2 15%, N3 5%
    """
    if levels is None:
        levels = [Decimal('0.30'), Decimal('0.15'), Decimal('0.05')]

    fagni = d(fagni_revenue)
    commissions = []
    for i, rate in enumerate(levels):
        amount = (fagni * rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
        commissions.append({
            'niveau':    i + 1,
            'taux':      float(rate),
            'montant':   int(amount),
        })
    return commissions


def format_receipt(pricing):
    """
    Générer un récapitulatif lisible pour WhatsApp.
    """
    return (
        f"🧾 Récapitulatif FAGNI\n"
        f"━━━━━━━━━━━━━━\n"
        f"🧺 Pressing     : {pricing['pressing_amount']:,} FCFA\n"
        f"🚗 Livraison    : {pricing['delivery_fee']:,} FCFA\n"
        f"⚡ Service fee  : {pricing['service_fee']:,} FCFA\n"
        f"━━━━━━━━━━━━━━\n"
        f"💰 TOTAL        : {pricing['total_client']:,} FCFA\n"
    )
