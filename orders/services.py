from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum


def recompute_order_pricing_for_laundry_partner(order, partner) -> bool:
    """
    Sprint P0, Wave 3 (BC1). Extrait tel quel (aucune formule/pourcentage
    modifié) le recalcul distance + pricing auparavant en ligne dans
    orders/ops_api.py::ops_assign_partner, pour que ce recalcul soit
    partagé à l'identique entre l'affectation manuelle OPS et
    l'auto-affectation BC1 (orders/client_api.py::api_create_order) —
    les deux doivent produire exactement le même prix final pour un même
    partenaire/commande.

    Ne sauvegarde jamais order.save() elle-même : mute les attributs en
    mémoire (delivery_fee, distances, total_client_ttc/total/service_fee/
    amount_laundry_partner/fagni_revenue_ht/margin_net) et laisse
    l'appelant décider du update_fields et du moment de l'écriture —
    exactement comme le faisait ops_assign_partner avant extraction.

    Retourne True si le recalcul a été effectué (partenaire géolocalisé,
    distance obtenue, moteur pricing exécuté sans erreur), False sinon —
    dans ce cas l'appelant doit conserver le pricing existant de la
    commande tel quel (jamais un prix incohérent/à moitié recalculé).
    """
    if not (partner and getattr(partner, 'latitude', None) and getattr(partner, 'longitude', None)):
        return False

    from orders.utils.distances import osrm_distance_km
    from orders.config_models import GlobalPricingSettings
    from decimal import Decimal as _D

    _cfg = GlobalPricingSettings.get_solo()
    _price_km = _D(str(_cfg.delivery_price_per_km or 150))
    _min_fee = _D(str(_cfg.delivery_min_fee or 2000))
    client_lat = order.pickup_lat or order.delivery_lat
    client_lng = order.pickup_lng or order.delivery_lng
    dist = osrm_distance_km(
        client_lat, client_lng,
        float(partner.latitude), float(partner.longitude)
    )
    if dist is None:
        return False

    fee = max(_min_fee, dist * 2 * _price_km)
    fee_int = int(fee.quantize(_D('1')))
    order.delivery_fee = fee_int
    order.distance_km_pickup = dist
    order.distance_km_delivery = dist
    order.distance_km_total = dist * 2
    order.distance_km = dist * 2

    try:
        from orders.pricing_engine import calculate_order
        nb = order.items.aggregate(s=Sum('quantity'))['s'] or order.articles_count or 1
        pricing = calculate_order(nb, order.bag_size or 'small', delivery_fee=fee_int)
        order.total_client_ttc = pricing['total_client_ttc']
        order.total = pricing['total_client']
        order.service_fee = pricing['service_fee']
        order.amount_laundry_partner = pricing['amount_laundry_partner']
        order.fagni_revenue_ht = pricing['fagni_revenue_ht']
        order.margin_net = pricing['total_fagni']
        return True
    except Exception:
        import logging
        logging.getLogger("fagni.orders.services").exception(
            "Echec silencieux: recalcul pricing apres changement partenaire/distance | order_id=%s",
            getattr(order, "id", None),
        )
        return False


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


def trigger_post_payment_auto_assignment(order_id):
    """
    Déclenche l'affectation automatique pressing + livreur collecte après
    confirmation comptable définitive du paiement.

    Garanties :
    - respecte AUTO_ASSIGN_ON_CLIENT_ORDER ;
    - refuse les commandes inexistantes, annulées ou non payées ;
    - ne remplace jamais un pressing déjà affecté ;
    - ne remplace jamais un livreur de collecte déjà affecté ;
    - ne crée pas une deuxième DeliveryLeg pickup ;
    - ne propage jamais une exception vers le flux de paiement.
    """
    import logging

    from django.conf import settings

    logger = logging.getLogger("fagni.post_payment_assignment")

    result = {
        "triggered": False,
        "laundry_assigned": False,
        "driver_assigned": False,
        "reason": "",
    }

    if not getattr(settings, "AUTO_ASSIGN_ON_CLIENT_ORDER", False):
        result["reason"] = "flag_disabled"
        logger.info(
            "Affectation post-paiement ignorée : flag désactivé | order_id=%s",
            order_id,
        )
        return result

    try:
        from orders.models import DeliveryLeg, Order

        order = (
            Order.objects
            .select_related("laundry_partner", "pickup_driver")
            .filter(pk=order_id)
            .first()
        )

        if not order:
            result["reason"] = "order_not_found"
            return result

        if order.status == "canceled":
            result["reason"] = "order_canceled"
            return result

        if order.payment_status != "paid":
            result["reason"] = "payment_not_confirmed"
            return result

        existing_pickup_leg = (
            DeliveryLeg.objects
            .filter(order=order, leg_type="pickup")
            .select_related("driver")
            .first()
        )

        laundry_already_assigned = bool(order.laundry_partner_id)
        driver_already_assigned = bool(
            order.pickup_driver_id
            or (
                existing_pickup_leg
                and existing_pickup_leg.driver_id
            )
        )

        result["laundry_assigned"] = laundry_already_assigned
        result["driver_assigned"] = driver_already_assigned

        if laundry_already_assigned and driver_already_assigned:
            result["triggered"] = True
            result["reason"] = "already_fully_assigned"

            logger.info(
                "Affectation post-paiement déjà complète | "
                "order_id=%s | laundry_id=%s | pickup_driver_id=%s",
                order.id,
                order.laundry_partner_id,
                order.pickup_driver_id,
            )
            return result

        from orders.client_api import _bc1_auto_assign_pickup_and_laundry

        assignment = _bc1_auto_assign_pickup_and_laundry(
            order,
            assign_laundry=not laundry_already_assigned,
            assign_driver=not driver_already_assigned,
        )

        order.refresh_from_db(
            fields=[
                "laundry_partner",
                "pickup_driver",
                "payment_status",
                "status",
            ]
        )

        result["triggered"] = True
        result["laundry_assigned"] = bool(order.laundry_partner_id)
        result["driver_assigned"] = bool(order.pickup_driver_id)
        result["reason"] = "assignment_executed"

        logger.info(
            "Affectation post-paiement exécutée | "
            "order_id=%s | helper_result=%s | "
            "laundry_id=%s | pickup_driver_id=%s",
            order.id,
            assignment,
            order.laundry_partner_id,
            order.pickup_driver_id,
        )

        return result

    except Exception:
        result["reason"] = "unexpected_error"

        logger.exception(
            "Échec non bloquant de l'affectation post-paiement | order_id=%s",
            order_id,
        )

        return result
