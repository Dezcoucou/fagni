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
            pass  # debug supprimé

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
        import logging
        logging.getLogger("fagni.orders.service_layer.payouts").exception("Exception silencieuse (auto-log) - fichier=orders/service_layer/payouts.py ligne=35")

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

    # ✅ fallback: si jambe sans driver, prendre le livreur historique de la
    # commande - UNIQUEMENT celui correspondant au type de la jambe. Une
    # jambe pickup ne doit jamais etre payee via order.delivery_partner
    # (livreur retour), et inversement.
    # Pour Crowd : utiliser leg.cotransporter
    if order and not driver:
        # Vérifier si c'est une jambe Crowd
        actor_type = getattr(leg, "actor_type", "")
        if actor_type == "cotransporter":
            # Pour Crowd, on n'utilise pas le fallback order.pickup_driver
            # On garde driver = None et on gérera le wallet cotransporteur plus bas
            pass
        else:
            leg_type = (getattr(leg, "leg_type", "") or "").strip().lower()
            try:
                if leg_type == "pickup":
                    driver = getattr(order, "pickup_driver", None)
                elif leg_type == "return":
                    driver = getattr(order, "delivery_partner", None)
                else:
                    driver = None
            except Exception:
                driver = None

    # Pour Crowd, on accepte cotransporter à la place de driver
    actor_type = getattr(leg, "actor_type", "professional")
    has_actor = bool(driver) or (actor_type == "cotransporter" and getattr(leg, "cotransporter_id", None))

    if not order or not has_actor:
        _dbg(
            "SKIP: missing order/actor",
            "order?", bool(order),
            "driver?", bool(driver),
            "cotransporter?", getattr(leg, "cotransporter_id", None),
            "actor_type=", actor_type,
            "order.delivery_partner_id=", getattr(order, "delivery_partner_id", None) if order else None,
            "leg.driver_id=", getattr(leg, "driver_id", None),
        )
        return None

    # ✅ order peut être stale -> refresh minimal avant de trancher
    if getattr(order, "payment_status", None) != "paid":
        try:
            order.refresh_from_db(fields=["payment_status", "delivery_partner_id"])
        except Exception:
            import logging
            logging.getLogger("fagni.orders.service_layer.payouts").exception("Exception silencieuse (auto-log) - fichier=orders/service_layer/payouts.py ligne=75")

    if getattr(order, "payment_status", None) != "paid":
        _dbg("SKIP: order not paid", "order.payment_status=", getattr(order, "payment_status", None))
        return None

    # 🔄 P0.4 FIX: Refresh from DB to ensure driver_amount is up-to-date after signal recompute
    try:
        leg.refresh_from_db(fields=["driver_amount", "fagni_margin"])
    except Exception:
        pass
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
        # P0.4 (A11.30) : suppression du fallback Order.amount_driver_partner.
        # Si leg.driver_amount est nul, c'est une anomalie a logger.
        # Aucun payout n'est cree, aucune WalletTransaction n'est generee.
        import logging
        logging.getLogger("fagni.payouts").warning(
            "Payout ignore : leg.driver_amount=0 | leg_id=%s | order_id=%s",
            getattr(leg, "id", None),
            getattr(order, "id", None),
        )
        return None

    from wallets.services import get_or_create_wallet_for_delivery_partner, credit_wallet
    from wallets.models import WalletTransaction

    # Déterminer le wallet selon le type d'acteur
    actor_type = getattr(leg, "actor_type", "professional")
    if actor_type == "cotransporter":
        from crowd.wallets import get_or_create_wallet_for_cotransporter
        cotransporter = getattr(leg, "cotransporter", None)
        wallet = get_or_create_wallet_for_cotransporter(cotransporter)
        if not wallet:
            _dbg("SKIP: no wallet for cotransporter")
            return None
    else:
        wallet = get_or_create_wallet_for_delivery_partner(driver)

    # Anti-doublon dur (par jambe) - verrou DB pour serialiser les appels concurrents
    from orders.models import DeliveryLeg
    
    with transaction.atomic():
        # Verrouille la ligne du leg pour la duree de la transaction (bloque les appels concurrents)
        DeliveryLeg.objects.select_for_update().filter(pk=leg.pk).first()
    
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
