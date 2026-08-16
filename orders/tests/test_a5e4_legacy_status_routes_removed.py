from django.test import TestCase

from orders.models import Customer, Order


class LegacyGenericStatusRoutesRemovedTests(TestCase):
    """
    Contrat A5-E4.

    Les anciennes routes génériques de mutation du statut Order ne font plus
    partie de l'architecture active FAGNI.

    Les transitions restent portées par :
    - les workflows métier spécialisés ;
    - l'API OPS authentifiée pour les actions génériques d'exploitation.
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client A5-E4",
            phone="0700009840",
        )
        self.order = Order.objects.create(
            customer=self.customer,
            status="pending",
        )

    def test_legacy_update_status_route_is_not_exposed(self):
        response = self.client.post(
            f"/orders/{self.order.id}/status/",
            {"status": "done"},
        )

        self.assertEqual(response.status_code, 404)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")

    def test_legacy_change_status_route_is_not_exposed(self):
        response = self.client.post(
            f"/orders/{self.order.id}/status/change/",
            {"status": "done"},
        )

        self.assertEqual(response.status_code, 404)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")

    def test_get_does_not_expose_legacy_change_status_route(self):
        response = self.client.get(
            f"/orders/{self.order.id}/status/change/",
        )

        self.assertEqual(response.status_code, 404)

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")
