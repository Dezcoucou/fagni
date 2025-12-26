from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum


def recompute_order_distance_from_legs(order, save: bool = True) -> Decimal:
    """
    Recalcule la distance totale de mission pour une commande FAGNI
    à partir des DeliveryLeg associés (order.legs).

    ✅ Règle de cohérence :
    - La distance de référence côté Order est distance_km_total.
    - distance_km est legacy/compat et doit suivre distance_km_total.
    - Si aucune distance leg n'est dispo, on NE GARDE PAS une ancienne distance_km "fantôme".
      -> on retourne la valeur actuelle si elle existe, mais si save=True on peut la neutraliser.

    Logique :
    - On additionne toutes les distance_km NON NULL des legs.
    - Si AUCUNE distance n'est renseignée :
        ➜ on retourne juste la valeur actuelle (ou 0 si vide)
        ➜ et si save=True : on met distance_km = None (anti-phantom)
    - Si save=True et qu'on a une somme, on met à jour distance_km_total ET distance_km.
    """
    if not hasattr(order, "legs"):
        return order.distance_km_total or order.distance_km or Decimal("0.00")

    agg = order.legs.filter(distance_km__isnull=False).aggregate(s=Sum("distance_km"))
    total = agg["s"]

    # Aucune distance sur les legs
    if total is None:
        if save:
            # Anti-phantom : si pas de legs distances, on évite de garder une vieille valeur
            order.distance_km = None
            # On ne touche pas distance_km_total ici (il est piloté ailleurs)
            order.save(update_fields=["distance_km"])
        return order.distance_km_total or order.distance_km or Decimal("0.00")

    if not isinstance(total, Decimal):
        total = Decimal(str(total))

    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if save:
        # On synchronise les deux champs
        order.distance_km_total = total
        order.distance_km = total
        order.save(update_fields=["distance_km_total", "distance_km"])

    return total
