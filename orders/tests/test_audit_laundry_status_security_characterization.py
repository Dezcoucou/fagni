"""
Audit parcours production/blanchisserie V1 - Etape 2 : tests de
caracterisation, aucune correction de production. Couvre
orders:laundry_update_status (orders/views.py::laundry_update_status).

Constats verifies directement (shell de test, avant ecriture de ces tests) :
- la vue n'a AUCUN decorateur d'authentification (seul @require_POST est
  present) : un POST non authentifie modifie reellement Order.status ;
- la vue ne verifie jamais order.laundry_partner_id : n'importe quel appelant
  peut modifier n'importe quelle commande ;
- action="accept" ecrit order.status="accepted", une valeur absente de
  Order.STATUS_CHOICES ;
- action="done" ecrit order.status="done" sans aucune verification des
  DeliveryLeg (viole la regle deja securisee sur partner_api.py dans le lot
  logistique precedent - cette vue-ci n'a jamais ete corrigee) ;
- une commande "canceled" est reactivee en "in_progress" par action="start" ;
- une action inconnue renvoie tout de meme HTTP 200 {"success": true} sans
  modifier le statut (pas de rejet explicite).

Ces tests expriment le comportement CIBLE et echouent donc majoritairement
avant correction - c'est le resultat attendu de cette Etape 2.
"""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orders.models import Customer, Order
from partners.models import LaundryPartner

User = get_user_model()


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_laundry(phone, email):
    return LaundryPartner.objects.create(name="Pressing Audit", phone=phone, email=email, is_active=True)


def _make_laundry_user(laundry):
    return User.objects.create_user(
        username=f"laundry_{laundry.id}", email=laundry.email, password="x",
    )


def _make_order(phone, laundry=None, status="pending"):
    return Order.objects.create(
        customer=_make_customer(phone),
        laundry_partner=laundry,
        status=status,
        total_client_ttc=Decimal("1000"),
    )


def _post_status(client, order, action):
    return client.post(
        reverse("orders:laundry_update_status", args=[order.id]),
        data={"action": action},
    )


class LaundryUpdateStatusSecurityCharacterizationTests(TestCase):
    def test_unauthenticated_request_is_refused(self):
        laundry = _make_laundry("0700010101", "own1@example.com")
        order = _make_order("0700010001", laundry=laundry)

        resp = _post_status(self.client, order, "start")

        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 200,
            "une requete non authentifiee ne doit jamais pouvoir modifier le statut",
        )
        self.assertEqual(order.status, "pending", "aucune modification ne doit avoir lieu sans authentification")

    def test_foreign_laundry_cannot_modify_order(self):
        owner = _make_laundry("0700010102", "own2@example.com")
        foreign = _make_laundry("0700010103", "foreign2@example.com")
        foreign_user = _make_laundry_user(foreign)
        order = _make_order("0700010002", laundry=owner)

        self.client.force_login(foreign_user)
        resp = _post_status(self.client, order, "start")

        order.refresh_from_db()
        self.assertNotEqual(resp.status_code, 200, "une blanchisserie etrangere ne doit pas pouvoir agir")
        self.assertEqual(order.status, "pending", "aucune modification par une blanchisserie non proprietaire")

    def test_action_done_never_forces_order_done(self):
        owner = _make_laundry("0700010104", "own3@example.com")
        owner_user = _make_laundry_user(owner)
        order = _make_order("0700010003", laundry=owner, status="in_progress")

        self.client.force_login(owner_user)
        _post_status(self.client, order, "done")

        order.refresh_from_db()
        self.assertNotEqual(
            order.status, "done",
            "le pressing ne doit jamais pouvoir forcer Order.status a 'done' directement",
        )

    def test_action_accept_never_produces_ghost_accepted_status(self):
        owner = _make_laundry("0700010105", "own4@example.com")
        owner_user = _make_laundry_user(owner)
        order = _make_order("0700010004", laundry=owner, status="pending")

        self.client.force_login(owner_user)
        _post_status(self.client, order, "accept")

        order.refresh_from_db()
        self.assertNotEqual(
            order.status, "accepted",
            "'accepted' est un statut fantome, absent de Order.STATUS_CHOICES",
        )
        self.assertIn(order.status, dict(Order.STATUS_CHOICES), "le statut doit rester une valeur officielle")

    def test_action_start_produces_in_progress(self):
        owner = _make_laundry("0700010106", "own5@example.com")
        owner_user = _make_laundry_user(owner)
        order = _make_order("0700010005", laundry=owner, status="pending")

        self.client.force_login(owner_user)
        resp = _post_status(self.client, order, "start")

        order.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(order.status, "in_progress")

    def test_canceled_order_never_reactivated(self):
        owner = _make_laundry("0700010107", "own6@example.com")
        owner_user = _make_laundry_user(owner)
        order = _make_order("0700010006", laundry=owner, status="canceled")

        self.client.force_login(owner_user)
        _post_status(self.client, order, "start")

        order.refresh_from_db()
        self.assertEqual(order.status, "canceled", "une commande annulee ne doit jamais etre reactivee")

    def test_unknown_action_refused_without_modification(self):
        owner = _make_laundry("0700010108", "own7@example.com")
        owner_user = _make_laundry_user(owner)
        order = _make_order("0700010007", laundry=owner, status="in_progress")

        self.client.force_login(owner_user)
        resp = _post_status(self.client, order, "bogus_action")

        order.refresh_from_db()
        self.assertNotEqual(resp.status_code, 200, "une action inconnue doit etre explicitement refusee")
        self.assertEqual(order.status, "in_progress", "aucune modification pour une action inconnue")
