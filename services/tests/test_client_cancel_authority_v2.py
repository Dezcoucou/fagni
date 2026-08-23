import jwt

from django.conf import settings
from django.test import TestCase

from orders.models import Customer, Order
from services.models import Service, ServiceCategory
from services.services import create_service_execution


class ClientCancelAuthorityV2Tests(TestCase):

    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client Cancel Authority",
            phone="0700099901",
            address="Riviera 3",
        )

        self.category = ServiceCategory.objects.create(
            code="client-cancel-authority",
            name="Client Cancel Authority",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="client-cancel-authority-service",
            category=self.category,
            name="Client Cancel Authority Service",
            description="",
            is_active=True,
            primary_engine=Service.ENGINE_PICKUP_RETURN,
            requires_partner=False,
            requires_logistics=False,
            requires_weighing=False,
            requires_appointment=False,
            requires_quote=False,
            requires_asset=False,
            requires_otp=False,
            requires_signature=False,
            pricing_mode="fixed",
            default_sla_hours=24,
        )

    def auth_headers(self):
        token = jwt.encode(
            {
                "cid": self.customer.id,
                "name": self.customer.name,
            },
            settings.SECRET_KEY,
            algorithm="HS256",
        )

        return {
            "HTTP_AUTHORIZATION": f"Bearer {token}",
        }

    def test_v2_order_is_cancelled_through_canonical_authority(self):
        order = Order.objects.create(
            customer=self.customer,
            status="pending",
        )

        execution = create_service_execution(
            order=order,
            service=self.service,
        )

        order.refresh_from_db()
        execution.refresh_from_db()

        self.assertEqual(order.status, "pending")
        self.assertEqual(
            execution.status,
            "pending",
        )

        response = self.client.post(
            f"/api/client/orders/{order.id}/cancel/",
            **self.auth_headers(),
        )

        self.assertEqual(
            response.status_code,
            200,
            response.content.decode(),
        )

        payload = response.json()

        self.assertTrue(payload["canceled"])
        self.assertFalse(payload["late_fee"])

        order.refresh_from_db()
        execution.refresh_from_db()

        self.assertEqual(
            order.status,
            "canceled",
        )

        self.assertEqual(
            execution.status,
            "canceled",
        )

        self.assertEqual(
            payload["service_executions_canceled"],
            1,
        )

    def test_v1_order_can_still_be_cancelled(self):
        order = Order.objects.create(
            customer=self.customer,
            status="pending",
        )

        response = self.client.post(
            f"/api/client/orders/{order.id}/cancel/",
            **self.auth_headers(),
        )

        self.assertEqual(
            response.status_code,
            200,
            response.content.decode(),
        )

        order.refresh_from_db()

        self.assertEqual(
            order.status,
            "canceled",
            "Une commande V1 doit conserver le comportement historique.",
        )
