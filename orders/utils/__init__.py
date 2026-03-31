"""
Utilities pour l'app orders.

⚠️ IMPORTANT :
Ne pas faire d'import "lourd" ici (assignation, models, services),
pour éviter des imports circulaires au démarrage Django.
"""

from decimal import Decimal


def build_order_display_context(order, customer=None, amounts=None, paid=0):
    """
    Construit un contexte d'affichage robuste pour les templates commande.
    Évite les VariableDoesNotExist côté template.
    """
    amounts = amounts or {}

    try:
        total_amount = amounts.get("total_ttc", 0) or 0
    except Exception:
        total_amount = 0

    try:
        paid_amount = paid or 0
    except Exception:
        paid_amount = 0

    try:
        remain_amount = Decimal(str(total_amount)) - Decimal(str(paid_amount))
    except Exception:
        remain_amount = 0

    display_address = (
        getattr(order, "pickup_address", None)
        or getattr(customer, "address", None)
        or (
            getattr(getattr(order, "customer", None), "address", None)
            if getattr(order, "customer", None) else None
        )
        or "Adresse non renseignée"
    )

    return {
        "display_address": display_address,
        "total_amount": total_amount,
        "paid_amount": paid_amount,
        "remain_amount": remain_amount,
    }

from .canonical_snapshot import (
    canonical_order_status,
    canonical_payment_status,
    build_order_canonical_snapshot,
)
