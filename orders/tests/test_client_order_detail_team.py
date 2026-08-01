"""
Mission pilote propre - api_order_detail doit exposer pickup_driver/
pressing_info (deja consommes par OrderDetail/OrderTeamSection.jsx cote
fagni-client) des qu'un partenaire/livreur est reellement assigne (peu
importe si l'assignation vient d'OPS ou de l'auto-affectation BC1) -
jamais de donnee inventee si rien n'est assigne.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from orders.client_api import _make_token
from orders.models import Customer, Order
from partners.models import DeliveryPartner, LaundryPartner


def _make_order(customer, **extra):
    return Order.objects.create(
        customer=customer, pricing_mode="item", total_client_ttc=Decimal("5000"), **extra,
    )


class OrderDetailTeamInfoTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Client Test", phone="0700010001", address="Riviera 3")
        self.headers = {"HTTP_AUTHORIZATION": f"Bearer {_make_token(self.customer)}"}

    def _get(self, order):
        return self.client.get(
            reverse("api-client-order-detail", args=[order.id]), **self.headers
        )

    def test_aucune_affectation_champs_a_null(self):
        order = _make_order(self.customer)
        resp = self._get(order)
        body = resp.json()
        self.assertIsNone(body["pickup_driver"])
        self.assertIsNone(body["pressing_info"])

    def test_pressing_assigne_expose_initiales(self):
        partner = LaundryPartner.objects.create(name="Pressing Riviera", phone="0700000001")
        order = _make_order(self.customer, laundry_partner=partner)
        resp = self._get(order)
        body = resp.json()
        self.assertEqual(body["pressing_info"], {"initials": "PR"})
        self.assertIsNone(body["pickup_driver"])

    def test_livreur_collecte_assigne_expose_initiales_et_vehicule(self):
        driver = DeliveryPartner.objects.create(
            name="Jean Kouassi", phone="0700000002", vehicle_type="moto",
        )
        order = _make_order(self.customer, pickup_driver=driver)
        resp = self._get(order)
        body = resp.json()
        self.assertEqual(body["pickup_driver"], {"initials": "JK", "vehicle": "moto"})
        self.assertIsNone(body["pressing_info"])
