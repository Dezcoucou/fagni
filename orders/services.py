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


def _incrementer_parrainage_acteur(parrain_type, acteur_id, wallet_getter, order):
    """
    Fonction commune livreur/pressing (24 juillet 2026, suite) - meme
    logique que le type client (incrementer nb_actions, activer au
    seuil) mais declenchee sur un acteur d'execution (DeliveryPartner ou
    LaundryPartner), pas un Customer. Le seuil pour ces deux types est
    de 10 actions (contre 1 pour client), defini a la creation du
    Parrainage (REMUNERATIONS dans api_creer_parrainage_v2secure).
    """
    from orders.models import Parrainage

    parrainage = Parrainage.objects.filter(
        filleul_type=parrain_type,
        filleul_id=acteur_id,
        statut="inscrit",
    ).first()

    if not parrainage:
        return

    parrainage.nb_actions += 1
    if parrainage.nb_actions < parrainage.actions_requises:
        parrainage.save(update_fields=["nb_actions", "updated_at"])
        return

    parrainage.statut = "actif"
    parrainage.cash_active = True
    parrainage.save(update_fields=["nb_actions", "statut", "cash_active", "updated_at"])

    if parrainage.remuneration_parrain > 0:
        try:
            from wallets.services import credit_wallet
            wallet = wallet_getter(parrainage.parrain_id)
            if wallet is not None:
                credit_wallet(
                    wallet, parrainage.remuneration_parrain,
                    description=f"Recompense parrainage - filleul {parrainage.filleul_nom}",
                    order=order,
                    tx_type="parrainage",
                )
        except Exception:
            import logging
            logging.getLogger("fagni.orders.services").exception(
                "Echec silencieux credit parrainage %s | parrain_id=%s", parrain_type, parrainage.parrain_id
            )


def completer_parrainage_livreur_si_applicable(order):
    """
    Compte une action pour chaque livreur ayant reellement participe a
    cette commande (collecte et/ou livraison peuvent etre deux livreurs
    distincts - chacun compte separement s'il a son propre parrainage
    'inscrit' en attente).
    """
    from wallets.services import get_or_create_wallet_for_delivery_partner
    from partners.models import DeliveryPartner

    def _wallet_livreur(driver_id):
        try:
            driver = DeliveryPartner.objects.get(id=driver_id)
        except DeliveryPartner.DoesNotExist:
            return None
        return get_or_create_wallet_for_delivery_partner(driver)

    for driver_id in {getattr(order, "pickup_driver_id", None), getattr(order, "delivery_partner_id", None)}:
        if driver_id:
            _incrementer_parrainage_acteur("livreur", driver_id, _wallet_livreur, order)


def completer_parrainage_pressing_si_applicable(order):
    """Compte une action pour le pressing (LaundryPartner) ayant traite cette commande."""
    from wallets.services import get_or_create_wallet_for_laundry_partner
    from partners.models import LaundryPartner

    def _wallet_pressing(partner_id):
        try:
            partner = LaundryPartner.objects.get(id=partner_id)
        except LaundryPartner.DoesNotExist:
            return None
        return get_or_create_wallet_for_laundry_partner(partner)

    partner_id = getattr(order, "laundry_partner_id", None)
    if partner_id:
        _incrementer_parrainage_acteur("pressing", partner_id, _wallet_pressing, order)
