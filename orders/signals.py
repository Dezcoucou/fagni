# orders/signals.py
from __future__ import annotations

import threading
from typing import Set

from django.db import transaction
from django.db.models.signals import post_save, post_delete, pre_save
from django.dispatch import receiver

from .models import DeliveryLeg, Order


# Thread-local guard pour éviter re-entrance / boucles
_state = threading.local()


def _get_pending_set() -> Set[int]:
    s = getattr(_state, "pending_order_ids", None)
    if s is None:
        s = set()
        _state.pending_order_ids = s
    return s


def _get_pending_paid_set() -> Set[int]:
    s = getattr(_state, "pending_paid_order_ids", None)
    if s is None:
        s = set()
        _state.pending_paid_order_ids = s
    return s


def _schedule_sync_order_status(order_id: int) -> None:
    """
    Planifie un sync après commit DB (on_commit), avec garde-fou anti-boucles.

    Objectif:
    - Auto-heal SOFT (normalize_order_legs) si dispo
    - ✅ Sync legs (sync_delivery_legs_for_order) pour appliquer les règles métier
      (ex: return "assigned" uniquement si pickup "done")
    - Puis recalculer Order.status à partir des legs.

    Note:
    - On évite les boucles grâce au thread-local `pending_order_ids`.
    """
    if not order_id:
        return

    pending = _get_pending_set()
    if order_id in pending:
        return

    pending.add(order_id)

    def _run():
        try:
            from .models import sync_order_status_from_legs, sync_delivery_legs_for_order

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

            # ✅ IMPORTANT: appliquer les règles métier legs (idempotent)
            # (ex: return reste pending tant que pickup n'est pas done)
            try:
                sync_delivery_legs_for_order(order)
            except Exception:
                pass

            # ✅ Recalcul du statut Order depuis les legs
            try:
                sync_order_status_from_legs(order, save=True)
            except Exception:
                pass

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


def _schedule_payouts_for_paid_order(order_id: int) -> None:
    """
    ✅ Quand une commande devient PAID, on paye automatiquement tous les legs déjà "done"
    (idempotent via contraintes WalletTransaction + logique trigger_driver_payout_for_leg).
    """
    if not order_id:
        return

    pending = _get_pending_paid_set()
    if order_id in pending:
        return
    pending.add(order_id)

    def _run():
        try:
            order = Order.objects.filter(pk=order_id).first()
            if not order:
                return

            # Ne rien faire si pas réellement paid
            if (getattr(order, "payment_status", "") or "").lower() != "paid":
                return

            # Ne jamais toucher une commande annulée
            if getattr(order, "status", None) == "canceled":
                return

            try:
                from orders.service_layer.payouts import trigger_driver_payout_for_leg
            except Exception:
                return

            legs = (
                DeliveryLeg.objects
                .filter(order=order, status="done")
                .exclude(status="canceled")
                .order_by("id")
            )

            for leg in legs:
                try:
                    trigger_driver_payout_for_leg(leg)
                except Exception:
                    # idempotent + safe
                    pass

        finally:
            pending.discard(order_id)

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
# ✅ CAPTURE ancien payment_status sur Order (pre_save)
# ============================================================
@receiver(pre_save, sender=Order, dispatch_uid="orders_order_pre_save_capture_old_payment_status")
def order_pre_save_capture_old_payment_status(sender, instance: Order, **kwargs):
    if not instance or not getattr(instance, "pk", None):
        instance._old_payment_status = None
        return

    try:
        old = Order.objects.filter(pk=instance.pk).values_list("payment_status", flat=True).first()
        instance._old_payment_status = old
    except Exception:
        instance._old_payment_status = None


# ============================================================
# ✅ post_save : 1) payout si transition -> done (après commit)
#             2) sync order (legs + statut) after commit
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

    # 2) ✅ Sync legs + statut commande depuis les legs (après commit)
    if order_id:
        _schedule_sync_order_status(int(order_id))


@receiver(post_delete, sender=DeliveryLeg, dispatch_uid="orders_deliveryleg_post_delete_sync_order_status")
def deliveryleg_post_delete_sync_order_status(sender, instance: DeliveryLeg, **kwargs):
    if not instance:
        return
    order_id = getattr(instance, "order_id", None)
    if order_id:
        _schedule_sync_order_status(int(order_id))


# ============================================================
# ✅ post_save Order : si payment_status devient "paid" -> payouts auto
# ============================================================
@receiver(post_save, sender=Order, dispatch_uid="orders_order_post_save_trigger_payouts_when_paid")
def order_post_save_trigger_payouts_when_paid(sender, instance: Order, created=False, **kwargs):
    if not instance or not getattr(instance, "pk", None):
        return

    try:
        old_ps = (getattr(instance, "_old_payment_status", None) or "").lower()
        new_ps = (getattr(instance, "payment_status", None) or "").lower()
        became_paid = (new_ps == "paid" and old_ps != "paid")
        if became_paid:
            _schedule_payouts_for_paid_order(int(instance.pk))
    except Exception:
        pass


# ── Notification WhatsApp partenaire (wa.me) ──────────────────
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=Order, dispatch_uid="notify_partner_whatsapp")
def notify_partner_on_assignment(sender, instance, created, **kwargs):
    """
    Quand une commande est assignée à une blanchisserie,
    génère et stocke le lien WhatsApp dans les notes de la commande.
    """
    try:
        partner = instance.laundry_partner
        if not partner or not partner.phone:
            return

        phone = partner.phone.replace(' ', '').replace('-', '')
        if not phone.startswith('+'):
            phone = '225' + phone.lstrip('0')

        code = instance.code or str(instance.id)
        bag = {'small': 'Petit sac', 'medium': 'Sac moyen', 'large': 'Grand sac'}.get(
            instance.bag_size or '', 'Sac'
        )

        if instance.status == 'pending' and instance.laundry_partner_id:
            msg = (
                f"🧺 Bonjour {partner.name} !\n\n"
                f"Nouvelle commande FAGNI reçue.\n"
                f"Code : *{code}*\n"
                f"Taille : *{bag}*\n\n"
                f"Le livreur FAGNI va déposer le sac chez vous.\n"
                f"Connectez-vous sur fagni-partner.vercel.app"
            )
            encoded = msg.replace(' ', '%20').replace('\n', '%0A')
            wa_link = f"https://wa.me/{phone}?text={encoded}"

            # Stocker le lien dans les notes si pas déjà présent
            notes = instance.notes or ''
            if 'WA_PARTNER:' not in notes:
                Order.objects.filter(pk=instance.pk).update(
                    notes=notes + f'\nWA_PARTNER:{wa_link}'
                )
    except Exception:
        pass
