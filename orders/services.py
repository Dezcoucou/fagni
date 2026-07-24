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
    if not hasattr(order, "legs") or order.pk is None:
        # order.pk is None : instance pas encore inseree en base (ex: appelee
        # depuis Order.save() avant l'INSERT initial) - aucune relation legs
        # n'est encore interrogeable, evite le ValueError Django sinon leve.
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


def completer_parrainage_client_si_applicable(order):
    """
    Si le client de cette commande est un filleul avec un parrainage
    'inscrit' en attente, incremente nb_actions et complete si le seuil
    est atteint (actions_requises, generalement 1 pour un client) -
    credite alors le wallet du parrain. Fire-and-forget par construction
    (appele dans un bloc deja try/except dans apply_order_payment),
    ne doit jamais faire echouer le paiement lui-meme.

    Scope volontairement limite au type 'client' ce soir (24 juillet
    2026) - livreur/pressing (10 actions requises) necessitent une
    logique de comptage plus large, non traitee ici, notee comme dette
    technique separee.
    """
    from orders.models import Parrainage
    from wallets.services import get_or_create_wallet_for_customer, credit_wallet

    customer = getattr(order, "customer", None)
    if not customer:
        return

    parrainage = Parrainage.objects.filter(
        filleul_type="client",
        filleul_id=customer.id,
        statut="inscrit",
    ).first()

    if not parrainage:
        return

    parrainage.nb_actions += 1
    if parrainage.nb_actions < parrainage.actions_requises:
        parrainage.save(update_fields=["nb_actions", "updated_at"])
        return

    # Seuil atteint - active la recompense cash et credite le parrain
    parrainage.statut = "actif"
    parrainage.cash_active = True
    parrainage.save(update_fields=["nb_actions", "statut", "cash_active", "updated_at"])

    if parrainage.parrain_type == "client" and parrainage.remuneration_parrain > 0:
        from orders.models import Customer
        try:
            parrain_customer = Customer.objects.get(id=parrainage.parrain_id)
            wallet = get_or_create_wallet_for_customer(parrain_customer)
            credit_wallet(
                wallet, parrainage.remuneration_parrain,
                description=f"Recompense parrainage - filleul {parrainage.filleul_nom or customer.name}",
                order=order,
                tx_type="parrainage",
            )
        except Customer.DoesNotExist:
            pass
