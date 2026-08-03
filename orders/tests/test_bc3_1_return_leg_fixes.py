"""
BC3.1 - correctifs de deux regressions reelles observees en production apres
activation de AUTO_ASSIGN_RETURN_DRIVER (BC3), sur `main`.

IMPORTANT : les deux causes racines sont des bugs PRE-EXISTANTS a BC3 (aucune
ligne de orders/partner_api.py::_bc3_auto_assign_return_driver n'est en
cause ni modifiee par ce patch) - BC3 les a seulement rendus visibles et
frequents en generant systematiquement les etats qui les declenchent (jambe
return deja assignee AVANT le premier clic OPS, jambe return creee alors
que order.status vient d'etre mis a "ready").

Bug 1 - reaffectation OPS du livreur retour "ne prend pas" (chemin complet) :
`ops_assign_return_driver` (orders/ops_api.py) faisait, dans cet ordre :
  1. `leg.driver = driver; leg.save(update_fields=[...])`
     -> declenche le post_save de DeliveryLeg
     (orders/signals.py::deliveryleg_post_save_sync_order_status), qui,
     comme aucune vue de ce depot n'est enveloppee dans un
     `transaction.atomic()` ni ATOMIC_REQUESTS, execute IMMEDIATEMENT et
     SYNCHRONEMENT (meme thread, meme requete) `sync_delivery_legs_for_order`
     (orders/models.py). Cette fonction fait SA PROPRE lecture fraiche de
     `order.delivery_partner_id` en base pour reappliquer `leg.driver_id`
     (`if target_driver_id: leg.driver_id = target_driver_id`).
  2. `order.delivery_partner = driver; order.save(...)` - APRES l'etape 1.
  Consequence : au moment ou l'etape 1 relit `order.delivery_partner_id`
  en base, l'etape 2 n'a PAS ENCORE ete executee - la lecture renvoie
  l'ANCIEN driver (ou None au tout premier appel) et l'ecrase sur la
  jambe. `order.delivery_partner` finit par pointer sur le nouveau driver
  (etape 2, apres coup), mais `DeliveryLeg.driver` - le champ lu par l'app
  livreur (driver_missions/driver_pending_mission) et les paiements -
  reste sur l'ANCIEN driver. La variable responsable : `target_driver_id`
  dans `sync_delivery_legs_for_order`, lu AVANT que la vue n'ait persiste
  la nouvelle valeur qu'il est censé refleter. Cette course n'affecte QUE
  la REAFFECTATION (2e appel, driver deja present) : au tout premier appel
  BC3, `order.delivery_partner_id` est encore None, `target_driver_id` est
  donc falsy et la ligne litigieuse ne s'execute pas - d'ou l'illusion que
  "avant BC3 (= avant qu'il y ait une reaffectation a faire) ca marchait".
  Un second bug, plus visible mais secondaire, aggravait le symptome sur
  le meme code : le garde `leg.status not in ("assigned","in_progress","done")`
  empechait aussi bien `assigned_now` (donc toute notification au nouveau
  livreur) que la mise a jour explicite du champ driver des qu'une jambe
  etait deja "assigned" - desormais systematique une fois BC3 actif.

Bug 2 - le statut "Pret" revient a "in_progress" apres rafraichissement :
meme mecanisme de course, sur `sync_order_status_from_legs` cette fois.
`partner_update_status` (orders/partner_api.py) met `order.status = "ready"`
et sauvegarde, PUIS cree la DeliveryLeg return (`get_or_create`, deja
present avant BC3). Cette creation declenche, de la meme facon
synchrone, `sync_order_status_from_legs(order, save=True)`
(orders/models.py). Cette fonction ne connait que pending/in_progress/done
- jamais "ready" - et recalcule/ecrase Order.status a partir des legs : le
pickup etant deja "assigned"/"done" (cas normal une fois BC1 actif ou la
collecte reellement terminee), au moins un leg actif => elle sauvegarde
"in_progress", ecrasant silencieusement le "ready" qui vient d'etre commit
une ligne plus haut. La reponse HTTP renvoie encore le mot "ready"
(variable locale `new_status`, jamais re-lue depuis la base), donc
l'ecriture invisible n'apparait qu'au prochain fetch ("apres
rafraichissement"). La variable responsable : `new_status` calcule dans
`sync_order_status_from_legs`, qui ne sait produire que
pending/in_progress/done et n'a aucune notion de "ready".

Pourquoi seulement visible avec TransactionTestCase (et en production) :
`django.test.TestCase` enveloppe chaque test dans une transaction non
commitee ; les callbacks `transaction.on_commit` ne se declenchent alors
jamais - mais ici `_schedule_sync_order_status` ne PASSE MEME PAS par
`on_commit` (aucune vue n'etant atomique), donc le probleme est bel et
bien synchrone et deterministe en production. `TransactionTestCase` est
utilise ici uniquement pour eviter tout autre effet de bord lie a
l'enveloppe transactionnelle du test lui-meme.

Correctifs (aucune migration) :
- ops_api.py (`ops_assign_return_driver`) : sauvegarde
  `order.delivery_partner`/`cost_driver_delivery` AVANT toute sauvegarde de
  la DeliveryLeg return, pour que la relecture synchrone declenchee par le
  signal trouve deja la bonne valeur (fin de la course). Retire aussi
  "assigned" du tuple qui bloquait a la fois la notification et la mise a
  jour explicite du driver (seuls in_progress/done restent proteges, comme
  avant).
- models.py (`sync_order_status_from_legs`) : ajoute "ready" a la garde
  deja existante pour "canceled" (meme principe deja applique par le code:
  un statut pilote explicitement ailleurs ne doit jamais etre
  recalcule/ecrase ici).
"""
import json

import jwt
from django.conf import settings
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from orders.client_api import _make_token
from orders.models import Customer, DeliveryLeg, Order
from partners.models import DeliveryPartner, LaundryPartner


RIVIERA_LAT = 5.360
RIVIERA_LNG = -3.950


def _token_partner(partner):
    return jwt.encode({'pid': partner.id, 'name': partner.name}, settings.SECRET_KEY, algorithm='HS256')


def _partner_headers(partner):
    return {'HTTP_AUTHORIZATION': f'Bearer {_token_partner(partner)}'}


def _token_ops():
    return jwt.encode({'ops': True, 'name': 'Test OPS'}, settings.SECRET_KEY, algorithm='HS256')


def _ops_headers():
    return {'HTTP_AUTHORIZATION': f'Bearer {_token_ops()}'}


def _make_laundry(phone="0700000301"):
    return LaundryPartner.objects.create(
        name="Pressing BC3.1", phone=phone, is_active=True,
        latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
    )


def _make_driver(name, phone):
    return DeliveryPartner.objects.create(
        name=name, phone=phone, is_active=True,
        latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
    )


def _make_order_with_assigned_pickup_leg(laundry, pickup_driver, phone="0700006001"):
    """
    Reproduit l'etat reel au moment ou le pressing marque PRET : le livreur a
    physiquement termine la collecte (leg pickup "done") - sinon le pressing
    n'aurait pas le sac a laver. C'est cet etat, et pas "assigned", qui
    correspond au flux production reel (driver_confirm_pickup marque le
    pickup "done" avant que le pressing puisse laver puis marquer "ready").
    """
    customer = Customer.objects.create(name="Client BC3.1", phone=phone, address="Riviera 3")
    order = Order.objects.create(
        customer=customer, laundry_partner=laundry, status="in_progress",
        pickup_address="Riviera 3", pickup_lat=RIVIERA_LAT, pickup_lng=RIVIERA_LNG,
        delivery_address="Riviera 3", delivery_lat=RIVIERA_LAT, delivery_lng=RIVIERA_LNG,
    )
    DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=pickup_driver, status="done")
    return order


class Bug2StatusPretNeRevientPlusAInProgress(TransactionTestCase):
    """
    Symptome 2 : apres relecture fraiche en base ("apres rafraichissement"),
    order.status doit rester "ready", que le flag BC3 soit actif ou non -
    la cause est independante de BC3.
    """

    @override_settings(AUTO_ASSIGN_RETURN_DRIVER=False)
    def test_statut_pret_stable_flag_bc3_desactive(self):
        laundry = _make_laundry(phone="0700000302")
        driver = _make_driver("Livreur Collecte 1", "0700000310")
        order = _make_order_with_assigned_pickup_leg(laundry, driver, phone="0700006002")

        resp = self.client.post(
            reverse('api-partner-status', args=[order.id]),
            data=json.dumps({'status': 'ready'}),
            content_type='application/json',
            **_partner_headers(laundry),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ready')

        fresh = Order.objects.get(pk=order.pk)
        self.assertEqual(fresh.status, 'ready', "le statut doit rester 'ready' apres relecture fraiche")

    @override_settings(AUTO_ASSIGN_RETURN_DRIVER=True)
    def test_statut_pret_stable_flag_bc3_active(self):
        laundry = _make_laundry(phone="0700000303")
        pickup_driver = _make_driver("Livreur Collecte 2", "0700000311")
        _make_driver("Livreur Retour", "0700000312")  # candidat pour BC3
        order = _make_order_with_assigned_pickup_leg(laundry, pickup_driver, phone="0700006003")

        resp = self.client.post(
            reverse('api-partner-status', args=[order.id]),
            data=json.dumps({'status': 'ready'}),
            content_type='application/json',
            **_partner_headers(laundry),
        )
        self.assertEqual(resp.status_code, 200)

        fresh = Order.objects.get(pk=order.pk)
        self.assertEqual(fresh.status, 'ready', "le statut doit rester 'ready' apres relecture fraiche, meme avec BC3 actif")
        # BC3 doit quand meme avoir fait son travail normalement.
        self.assertIsNotNone(fresh.delivery_partner_id)


@override_settings(AUTO_ASSIGN_RETURN_DRIVER=True)
class Bug1ReaffectationOpsSuitAuDriverReellementChange(TransactionTestCase):
    """
    Symptome 1 : apres une auto-affectation BC3, une reaffectation OPS vers
    un AUTRE livreur doit mettre a jour a la fois order.delivery_partner ET
    la DeliveryLeg return (source de verite pour l'app livreur/paiements).
    """

    def test_reaffectation_ops_met_a_jour_le_leg_pas_seulement_order(self):
        laundry = _make_laundry(phone="0700000304")
        pickup_driver = _make_driver("Livreur Collecte 3", "0700000313")
        order = _make_order_with_assigned_pickup_leg(laundry, pickup_driver, phone="0700006004")

        # BC3 auto-affecte un candidat actif (peu importe lequel : le pickup
        # lui-meme est un DeliveryPartner actif, donc eligible aussi).
        self.client.post(
            reverse('api-partner-status', args=[order.id]),
            data=json.dumps({'status': 'ready'}),
            content_type='application/json',
            **_partner_headers(laundry),
        )
        order.refresh_from_db()
        leg = DeliveryLeg.objects.get(order=order, leg_type="return")
        auto_assigned_driver_id = order.delivery_partner_id
        self.assertIsNotNone(auto_assigned_driver_id)
        self.assertEqual(leg.driver_id, auto_assigned_driver_id)

        # OPS reaffecte explicitement a un AUTRE livreur.
        other_driver = _make_driver("Livreur Retour Explicite", "0700000315")
        self.assertNotEqual(other_driver.id, auto_assigned_driver_id)
        reassign_resp = self.client.post(
            f"/api/ops/orders/{order.id}/assign-return-driver/",
            data=json.dumps({'driver_id': other_driver.id}),
            content_type='application/json',
            **_ops_headers(),
        )
        self.assertEqual(reassign_resp.status_code, 200)

        order.refresh_from_db()
        leg.refresh_from_db()
        self.assertEqual(order.delivery_partner_id, other_driver.id)
        self.assertEqual(leg.driver_id, other_driver.id, "la DeliveryLeg doit suivre la reaffectation OPS, pas seulement Order.delivery_partner")
        self.assertEqual(leg.status, "assigned")

    def test_reaffectation_ops_toujours_impossible_sur_leg_in_progress_ou_done(self):
        """Garde-fou preserve : une jambe deja demarree/terminee ne doit jamais etre reassignee."""
        laundry = _make_laundry(phone="0700000305")
        pickup_driver = _make_driver("Livreur Collecte 4", "0700000316")
        driver1 = _make_driver("Livreur Retour 3", "0700000317")
        driver2 = _make_driver("Livreur Retour 4", "0700000318")
        order = _make_order_with_assigned_pickup_leg(laundry, pickup_driver, phone="0700006005")

        DeliveryLeg.objects.create(
            order=order, leg_type="return", driver=driver1, status="in_progress",
        )
        from django.utils import timezone
        Order.objects.filter(pk=order.pk).update(wash_complete_time=timezone.now())

        reassign_resp = self.client.post(
            f"/api/ops/orders/{order.id}/assign-return-driver/",
            data=json.dumps({'driver_id': driver2.id}),
            content_type='application/json',
            **_ops_headers(),
        )
        self.assertEqual(reassign_resp.status_code, 200)

        leg = DeliveryLeg.objects.get(order=order, leg_type="return")
        self.assertEqual(leg.driver_id, driver1.id, "une jambe in_progress ne doit jamais changer de livreur")
        self.assertEqual(leg.status, "in_progress")
