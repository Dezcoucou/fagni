"""
Audit parcours production/blanchisserie V1 - Etape 2 : tests de
caracterisation, aucune correction de production. Couvre driver_weighing
(orders/views.py) et la machine a etats OrderWeighing.status.

Constats verifies directement (shell de test) avant ecriture :
- driver_weighing accepte weight_kg="0" et weight_kg="-3.5" (aucune
  verification de positivite, seule la parsabilite Decimal est testee) ;
- ow.status n'est verrouille cote livreur QUE pour "confirmed"
  (`is_locked = (ow.status == "confirmed")`) - "disputed" et "resolved" ne
  sont pas proteges : une nouvelle soumission ecrase le poids ET force
  ow.status="draft", ramenant une pesee "resolved" en "draft" ;
- l'ownership driver (_can_driver_touch_order) est deja correcte (heritee du
  lot 3 stabilite) : ceci est verifie comme deja-correct, pas un bug.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from orders.models import Customer, DeliveryLeg, Order, OrderWeighing
from partners.models import DeliveryPartner

User = get_user_model()


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_driver(phone, email):
    return DeliveryPartner.objects.create(name="Livreur Audit", phone=phone, email=email, is_active=True)


def _make_driver_user(driver):
    return User.objects.create_user(username=f"driver_{driver.id}", email=driver.email, password="x")


def _make_order_with_assigned_pickup(phone, driver):
    customer = _make_customer(phone)
    order = Order.objects.create(customer=customer, status="in_progress", total_client_ttc=Decimal("1000"))
    DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="in_progress")
    return order


def _post_weight(client, order, weight_kg):
    return client.post(reverse("orders:driver_weighing", args=[order.id]), data={"weight_kg": weight_kg})


class DriverWeighingStateMachineCharacterizationTests(TestCase):
    def test_refuses_zero_weight(self):
        driver = _make_driver("0700030101", "d1@example.com")
        user = _make_driver_user(driver)
        order = _make_order_with_assigned_pickup("0700030001", driver)

        self.client.force_login(user)
        _post_weight(self.client, order, "0")

        ow = OrderWeighing.objects.get(order=order)
        self.assertNotEqual(ow.weight_kg, Decimal("0"), "un poids nul doit etre refuse")

    def test_refuses_negative_weight(self):
        driver = _make_driver("0700030102", "d2@example.com")
        user = _make_driver_user(driver)
        order = _make_order_with_assigned_pickup("0700030002", driver)

        self.client.force_login(user)
        _post_weight(self.client, order, "-3.5")

        ow = OrderWeighing.objects.get(order=order)
        self.assertGreater(ow.weight_kg, Decimal("0"), "un poids negatif doit etre refuse")

    def test_allows_positive_weight_on_draft(self):
        driver = _make_driver("0700030103", "d3@example.com")
        user = _make_driver_user(driver)
        order = _make_order_with_assigned_pickup("0700030003", driver)

        self.client.force_login(user)
        resp = _post_weight(self.client, order, "4.20")

        ow = OrderWeighing.objects.get(order=order)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(ow.weight_kg, Decimal("4.20"))
        self.assertEqual(ow.status, "draft")

    def test_confirmed_weighing_is_read_only_for_driver(self):
        driver = _make_driver("0700030104", "d4@example.com")
        user = _make_driver_user(driver)
        order = _make_order_with_assigned_pickup("0700030004", driver)
        OrderWeighing.objects.create(order=order, status="confirmed", weight_kg=Decimal("5.0"))

        self.client.force_login(user)
        _post_weight(self.client, order, "9.9")

        ow = OrderWeighing.objects.get(order=order)
        self.assertEqual(ow.weight_kg, Decimal("5.0"), "une pesee confirmee est en lecture seule cote livreur")
        self.assertEqual(ow.status, "confirmed")

    def test_disputed_weighing_is_read_only_for_driver(self):
        driver = _make_driver("0700030105", "d5@example.com")
        user = _make_driver_user(driver)
        order = _make_order_with_assigned_pickup("0700030005", driver)
        OrderWeighing.objects.create(order=order, status="disputed", weight_kg=Decimal("5.0"))

        self.client.force_login(user)
        _post_weight(self.client, order, "9.9")

        ow = OrderWeighing.objects.get(order=order)
        self.assertEqual(ow.weight_kg, Decimal("5.0"), "une pesee disputee est en lecture seule cote livreur")
        self.assertEqual(ow.status, "disputed")

    def test_resolved_weighing_is_read_only_for_driver(self):
        driver = _make_driver("0700030106", "d6@example.com")
        user = _make_driver_user(driver)
        order = _make_order_with_assigned_pickup("0700030006", driver)
        OrderWeighing.objects.create(
            order=order, status="resolved", weight_kg=Decimal("5.0"), final_weight_kg=Decimal("6.0"),
        )

        self.client.force_login(user)
        _post_weight(self.client, order, "9.9")

        ow = OrderWeighing.objects.get(order=order)
        self.assertEqual(ow.weight_kg, Decimal("5.0"), "une pesee resolue est en lecture seule cote livreur")
        self.assertEqual(ow.status, "resolved")

    def test_new_submission_never_brings_resolved_back_to_draft(self):
        driver = _make_driver("0700030107", "d7@example.com")
        user = _make_driver_user(driver)
        order = _make_order_with_assigned_pickup("0700030007", driver)
        OrderWeighing.objects.create(
            order=order, status="resolved", weight_kg=Decimal("5.0"), final_weight_kg=Decimal("6.0"),
        )

        self.client.force_login(user)
        _post_weight(self.client, order, "9.9")

        ow = OrderWeighing.objects.get(order=order)
        self.assertNotEqual(ow.status, "draft", "une soumission ne doit jamais ramener 'resolved' vers 'draft'")

    def test_driver_not_assigned_cannot_access_order(self):
        assigned_driver = _make_driver("0700030108", "d8@example.com")
        other_driver = _make_driver("0700030109", "d9@example.com")
        other_user = _make_driver_user(other_driver)
        order = _make_order_with_assigned_pickup("0700030008", assigned_driver)

        self.client.force_login(other_user)
        resp = _post_weight(self.client, order, "4.0")

        self.assertEqual(resp.status_code, 302, "redirection loin de la course (deja protege, lot 3)")
        self.assertFalse(
            OrderWeighing.objects.filter(order=order).exists(),
            "aucune pesee ne doit etre creee pour un livreur non affecte",
        )
