# orders/service_layer/payouts.py
from decimal import Decimal
from django.db import transaction


def trigger_driver_payout_for_leg(leg):
    """
    Déclenche le paiement du livreur pour UNE jambe terminée (status='done').

    Règles:
    - La jambe doit être done
    - La commande doit être payée (payment_status='paid')
    - Anti-doublon DUR: 1 payout (in) par jambe via WalletTransaction.leg
    """
    if not leg or getattr(leg, "status", None) != "done":
        return None

    order = getattr(leg, "order", None)
    driver = getattr(leg, "driver", None)
    if not order or not driver:
        return None

    if getattr(order, "payment_status", None) != "paid":
        return None

    amount = Decimal(str(getattr(leg, "driver_amount", 0) or 0))
    if amount <= 0:
        return None

    from wallets.services import get_or_create_wallet_for_delivery_partner, credit_wallet
    from wallets.models import WalletTransaction, Wallet

    wallet = get_or_create_wallet_for_delivery_partner(driver)

    # 🔐 Anti-doublon dur (par jambe)
    if WalletTransaction.objects.filter(
        wallet=wallet,
        order=order,
        type="payout",
        direction="in",
        leg=leg,
    ).exists():
        return None

    with transaction.atomic():
        # (Optionnel) verrouille le wallet pendant l'update
        Wallet.objects.select_for_update().filter(pk=wallet.pk)

        return credit_wallet(
            wallet,
            amount,
            description=f"Commande {order.code} – paiement jambe #{leg.id}",
            order=order,
            tx_type="payout",
            leg=leg,
        )
