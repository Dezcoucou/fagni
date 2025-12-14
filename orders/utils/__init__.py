"""
Utilities pour l'app orders.

- get_pricing_settings / get_workflow_settings :
    chargent les réglages globaux (pricing, workflow).
- auto_assign_laundry / auto_assign_delivery :
    helpers appelés dans les vues. Ici on met une version
    simple / neutre pour ne rien casser, en attendant si besoin
    une logique plus avancée.
"""

from .settings_loader import get_pricing_settings, get_workflow_settings


def auto_assign_laundry(order, *args, **kwargs):
    """
    Version simple :

    - Si la commande a déjà une blanchisserie (order.laundry_partner),
      on la laisse telle quelle.
    - Sinon on ne fait rien (retourne None).

    On accepte *args et **kwargs pour rester compatible avec
    les appels existants (au cas où on passait d'autres paramètres).
    """
    commit = kwargs.get("commit", True)

    laundry = getattr(order, "laundry_partner", None)

    if commit and laundry is not None:
        # En théorie rien à faire, mais on garde un save léger
        order.laundry_partner = laundry
        order.save(update_fields=["laundry_partner"])

    return laundry


def auto_assign_delivery(order, *args, **kwargs):
    """
    Même principe que auto_assign_laundry, mais pour le livreur.

    - Si la commande a déjà un delivery_partner, on le garde.
    - Sinon on ne fait rien.

    On accepte aussi *args / **kwargs pour compatibilité.
    """
    commit = kwargs.get("commit", True)

    driver = getattr(order, "delivery_partner", None)

    if commit and driver is not None:
        order.delivery_partner = driver
        order.save(update_fields=["delivery_partner"])

    return driver
