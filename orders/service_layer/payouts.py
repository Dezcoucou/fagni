# orders/service_layer/payouts.py
from decimal import Decimal
from django.db import transaction
import os


def trigger_driver_payout_for_leg(leg):
    """
    Déclenche le paiement du livreur pour UNE jambe terminée (status='done').

    Règles:
    - La jambe doit être done
    - La commande doit être payée
    - Anti-doublon DUR: 1 payout (in) par jambe via WalletTransaction.leg
    - Si la tx existe déjà, on la retourne (idempotent + debug friendly)
    """

    def _dbg(*args):
        if os.environ.get("PAYOUT_DEBUG") == "1":
            print("[payouts.trigger]", *args)

    _dbg("ENTER leg_id=", getattr(leg, "id", None), "status=", getattr(leg, "status", None))

    if not leg:
        _dbg("SKIP: leg is None")
        return None

    # 🧱 Guard rail: status annulé => jamais payer
    # (utile si quelqu’un repasse status=done par erreur ailleurs)
    try:
        st = (getattr(leg, "status", "") or "").lower().strip()
        if st == "canceled":
            _dbg("SKIP: leg status=canceled")
            return None
    except Exception:
        pass

    # 🧱 Guard rail: jambe annulée => jamais payer
    # (utile si quelqu’un repasse status=done par erreur)
    if getattr(leg, "is_canceled", False) or getattr(leg, "canceled_at", None):
        _dbg(
            "SKIP: leg canceled flag present",
            "is_canceled=", getattr(leg, "is_canceled", None),
            "canceled_at=", getattr(leg, "canceled_at", None),
        )
        return None

    if getattr(leg, "status", None) != "done":
        _dbg("SKIP: leg not done")
        return None
    order = getattr(leg, "order", None)
    driver = getattr(leg, "driver", None)

    # ✅ fallback: si jambe sans driver, prendre le livreur de la commande
    if order and not driver:
        try:
            driver = getattr(order, "delivery_partner", None)
        except Exception:
            driver = None

    if not order or not driver:
        _dbg(
            "SKIP: missing order/driver",
            "order?", bool(order),
            "driver?", bool(driver),
            "order.delivery_partner_id=", getattr(order, "delivery_partner_id", None) if order else None,
            "leg.driver_id=", getattr(leg, "driver_id", None),
        )
        return None

    # ✅ order peut être stale -> refresh minimal avant de trancher
    if getattr(order, "payment_status", None) != "paid":
        try:
            order.refresh_from_db(fields=["payment_status", "delivery_partner_id"])
        except Exception:
            pass

    if getattr(order, "payment_status", None) != "paid":
        _dbg("SKIP: order not paid", "order.payment_status=", getattr(order, "payment_status", None))
        return None

    amount = Decimal(str(getattr(leg, "driver_amount", 0) or 0))
    _dbg(
        "CTX:",
        "order_id=", getattr(order, "id", None),
        "order.delivery_partner_id=", getattr(order, "delivery_partner_id", None),
        "leg.driver_id=", getattr(leg, "driver_id", None),
        "driver_id=", getattr(driver, "id", None) if driver else None,
        "amount=", amount,
    )

    if amount <= 0:
        # ✅ fallback: si driver_amount n’est pas sur la jambe
        fallback = Decimal(str(getattr(order, "amount_driver_partner", 0) or 0))
        if fallback > 0:
            try:
                from orders.models import DeliveryLeg
                done_count = DeliveryLeg.objects.filter(order=order, status="done").count()
                if done_count == 1:
                    amount = fallback
            except Exception:
                pass

        if amount <= 0:
            _dbg("SKIP: amount <= 0")
            return None

    from wallets.services import get_or_create_wallet_for_delivery_partner, credit_wallet
    from wallets.models import WalletTransaction

    wallet = get_or_create_wallet_for_delivery_partner(driver)

    # 🔐 Anti-doublon dur (par jambe) — on retourne la tx si elle existe déjà
    existing = WalletTransaction.objects.filter(
        wallet=wallet,
        order=order,
        type="payout",
        direction="in",
        leg=leg,
    ).order_by("-id").first()
    if existing:
        _dbg("EXISTS: tx_id=", existing.id, "leg_id=", getattr(leg, "id", None))
        return existing

    with transaction.atomic():
        tx = credit_wallet(
            wallet,
            amount,
            label=f"Commande {order.code} – paiement jambe #{leg.id}",
            order=order,
            tx_type="payout",
            leg=leg,
            idempotency_key=f"driver_payout:{leg.id}",
        )
        _dbg("CREATED: tx_id=", getattr(tx, "id", None), "leg_id=", getattr(leg, "id", None))
        return tx
