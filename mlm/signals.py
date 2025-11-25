from django.db.models.signals import post_save
from django.dispatch import receiver
from orders.models import Order

from .services import generate_mlm_commissions_for_order


@receiver(post_save, sender=Order)
def order_status_done_generate_mlm(sender, instance: Order, created, **kwargs):
    """
    À chaque sauvegarde de commande :
      -> si status == 'done', on tente de générer les commissions MLM.
    """

    # On ne génère que si la commande passe réellement à "done"
    if instance.status == "done":
        generate_mlm_commissions_for_order(instance)
