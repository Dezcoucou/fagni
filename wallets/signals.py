from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from wallets.models import WalletTransaction


@receiver(post_save, sender=WalletTransaction)
def reconcile_order_after_driver_payout(sender, instance: WalletTransaction, created: bool, **kwargs):
    if not created:
        return
    if not getattr(settings, "FAGNI_AUTO_RECONCILE_LOGISTICS", True):
        return
    if getattr(instance, "type", None) != "payout" or getattr(instance, "direction", None) != "in":
        return

    w = getattr(instance, "wallet", None)
    if not w or getattr(w, "owner_type", None) != "driver":
        return

    order = getattr(instance, "order", None)
    if not order:
        return

    fn = getattr(order, "recompute_logistics_from_legs", None)
    if callable(fn):
        try:
            fn(save_legs=True, save_order=True)
        except Exception:
            # ne bloque jamais un payout à cause d'un reconcile
            pass
