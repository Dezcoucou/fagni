from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from orders.client_api import _make_token
from orders.models import Customer, Order


class ClientAuthSecurityTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client Auth Security",
            phone="0700554400",
            address="Riviera 3",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status="pending",
            pricing_mode="item",
            total_client_ttc=Decimal("5000"),
        )

        self.headers = {
            "HTTP_AUTHORIZATION": f"Bearer {_make_token(self.customer)}"
        }

    def test_home_sans_auth_retourne_401(self):
        response = self.client.get(
            reverse("api-client-home")
        )

        self.assertEqual(response.status_code, 401)

    def test_orders_sans_auth_retourne_401(self):
        response = self.client.get(
            reverse("api-client-orders")
        )

        self.assertEqual(response.status_code, 401)

    def test_order_detail_sans_auth_retourne_401(self):
        response = self.client.get(
            reverse(
                "api-client-order-detail",
                args=[self.order.id],
            )
        )

        self.assertEqual(response.status_code, 401)

    def test_declare_wave_sans_auth_retourne_401(self):
        response = self.client.post(
            reverse(
                "api-client-declare-wave-payment",
                args=[self.order.id],
            ),
            data={
                "payment_reference": "WAVE-AUTH-001",
            },
        )

        self.assertEqual(response.status_code, 401)

    def test_token_invalide_retourne_401(self):
        response = self.client.get(
            reverse("api-client-home"),
            HTTP_AUTHORIZATION="Bearer token-totalement-invalide",
        )

        self.assertEqual(response.status_code, 401)

    def test_order_detail_avec_auth_continue_de_fonctionner(self):
        response = self.client.get(
            reverse(
                "api-client-order-detail",
                args=[self.order.id],
            ),
            **self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], self.order.id)

    def test_home_avec_auth_continue_de_fonctionner(self):
        response = self.client.get(
            reverse("api-client-home"),
            **self.headers,
        )

        self.assertEqual(response.status_code, 200)
