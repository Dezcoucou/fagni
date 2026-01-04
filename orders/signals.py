# orders/signals.py
from __future__ import annotations

import threading
from typing import Set

from django.db import transaction
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver

from .models import DeliveryLeg


# Thread-local guard pour éviter re-entrance / boucles
_state = threading.local()


def _get_pending_set() -> Set[int]:
    s = getattr(_state, "pending_order_ids", None)
    if s is None:
        s = set()
        _state.pending_order_ids = s
    return s


def _schedule_sync_order_status(order_id: int) -> None:
    """
    Planifie un sync du statut commande après commit DB (on_commit),
    avec garde-fou pour ne pas spammer / boucler si plusieurs legs changent.

    Objectif:
    - NE PAS réécrire les legs (pas de sync_delivery_legs_for_order ici)
    - Autoriser un auto-heal "soft" (normalize_order_legs) sans casser accept/start/finish
    - Puis recalculer Order.status à partir des legs.
    """
    if not order_id:
        return

    pending = _get_pending_set()
    if order_id in pending:
        return

    pending.add(order_id)

    def _run():
        try:
            from .models import Order, sync_order_status_from_legs

            order = Order.objects.filter(pk=order_id).first()
            if not order:
                return

            # Ne jamais toucher une commande annulée
            if getattr(order, "status", None) == "canceled":
                return

            # ✅ Auto-heal SOFT (idempotent) : pas de delete/recreate
            try:
                from .views import normalize_order_legs
                normalize_order_legs(order)
            except Exception:
                pass

            # ✅ Recalcul du statut Order depuis les legs
            sync_order_status_from_legs(order, save=True)

        finally:
            pending.discard(order_id)

    # Si on n'est pas dans une transaction atomique, on exécute tout de suite.
    # Sinon, on attend le commit (safe en web requests / atomic()).
    try:
        conn = transaction.get_connection()
        if not conn.in_atomic_block:
            _run()
            return
    except Exception:
        pass

    transaction.on_commit(_run)


# ============================================================
# ✅ CAPTURE ancien statut (pre_save) pour détecter transition -> done
# ============================================================
@receiver(pre_save, sender=DeliveryLeg, dispatch_uid="orders_deliveryleg_pre_save_capture_old_status")
def deliveryleg_pre_save_capture_old_status(sender, instance: DeliveryLeg, **kwargs):
    if not instance or not getattr(instance, "pk", None):
        instance._old_status = None
        return

    try:
        old = DeliveryLeg.objects.filter(pk=instance.pk).values_list("status", flat=True).first()
        instance._old_status = old
    except Exception:
        instance._old_status = None


# ============================================================
# ✅ post_save : 1) payout si transition -> done (après commit)
#             2) sync order status (after commit)
# ============================================================
@receiver(post_save, sender=DeliveryLeg, dispatch_uid="orders_deliveryleg_post_save_sync_order_status")
def deliveryleg_post_save_sync_order_status(sender, instance: DeliveryLeg, created=False, **kwargs):
    if not instance:
        return

    order_id = getattr(instance, "order_id", None)

    # 1) ✅ Payout livreur : UNIQUEMENT si transition vers done
    try:
        old_status = getattr(instance, "_old_status", None)
        new_status = getattr(instance, "status", None)

        became_done = (new_status == "done" and old_status != "done")
        if became_done:
            def _payout():
                try:
                    from orders.service_layer.payouts import trigger_driver_payout_for_leg
                    trigger_driver_payout_for_leg(instance)
                except Exception:
                    pass

            # payout après commit si on est en atomic
            try:
                conn = transaction.get_connection()
                if not conn.in_atomic_block:
                    _payout()
                else:
                    transaction.on_commit(_payout)
            except Exception:
                # fallback : tente direct
                _payout()
    except Exception:
        pass

    # 2) ✅ Sync du statut commande depuis les legs
    if order_id:
        _schedule_sync_order_status(int(order_id))


@receiver(post_delete, sender=DeliveryLeg, dispatch_uid="orders_deliveryleg_post_delete_sync_order_status")
def deliveryleg_post_delete_sync_order_status(sender, instance: DeliveryLeg, **kwargs):
    if not instance:
        return
    order_id = getattr(instance, "order_id", None)
    if order_id:
        _schedule_sync_order_status(int(order_id))
