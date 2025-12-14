from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum


def recompute_order_distance_from_legs(order, save: bool = True) -> Decimal:
    """
    Recalcule la distance totale de mission pour une commande FAGNI
    à partir des DeliveryLeg associés (order.legs).

    Logique :
    - On additionne toutes les distance_km NON NULL des legs.
    - Si AUCUNE distance n'est renseignée :
        ➜ on NE CHANGE PAS la valeur de order.distance_km
        ➜ on retourne juste la valeur actuelle (ou 0 si elle est vide).
    - Si save=True et qu'on a une somme, on met à jour order.distance_km.

    Cette distance représente typiquement :
      - Base / position livreur -> Client (collecte)
      - Client -> Blanchisserie
      - Retour livreur -> Blanchisserie pour récupérer le linge
      - Blanchisserie -> Client
      - (+ autres jambes éventuelles : retour base, relais, etc.)
    """

    # Si l'instance n'a pas de related_name "legs", on ne fait rien.
    if not hasattr(order, "legs"):
        return order.distance_km or Decimal("0.00")

    # On ne considère que les jambes ayant une distance renseignée
    agg = order.legs.filter(distance_km__isnull=False).aggregate(s=Sum("distance_km"))
    total = agg["s"]

    # ⚠️ Aucune distance sur les DeliveryLeg : on ne touche pas à order.distance_km
    if total is None:
        return order.distance_km or Decimal("0.00")

    # On force en Decimal avec 2 décimales
    if not isinstance(total, Decimal):
        total = Decimal(str(total))

    total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if save:
        order.distance_km = total
        order.save(update_fields=["distance_km"])

    return total
