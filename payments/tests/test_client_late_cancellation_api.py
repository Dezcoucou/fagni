import datetime
from decimal import Decimal
from unittest.mock import patch

import jwt

from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from orders.client_api import api_cancel_order
from orders.models import Customer, Order
from payments.models import CustomerCharge
from wallets.models import Wallet


class ClientLateCancellationApiTests(TestCase):

    def setUp(self):
        self.factory = APIRequestFactory()

        self.customer = Customer.objects.create(
            name="Client API A7",
            phone="0700999801",
            address="Riviera 3",
        )

        self.token = jwt.encode(
            {"cid": self.customer.id},
            settings.SECRET_KEY,
            algorithm="HS256",
        )

    def _post_cancel(self, order):
        request = self.factory.post(
            f"/api/client/orders/{order.id}/cancel/",
            {},
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        return api_cancel_order(request, order.id)

    def _make_order(self, *, pickup_delta_hours):
        pickup_dt = timezone.localtime(
            timezone.now() + datetime.timedelta(
                hours=pickup_delta_hours
            )
        )

        return Order.objects.create(
            customer=self.customer,
            status="pending",
            total_client_ttc=Decimal("5000.00"),
            pickup_scheduled_date=pickup_dt.date(),
            pickup_scheduled_time=pickup_dt.time().replace(
                microsecond=0,
                tzinfo=None,
            ),
        )

    def test_cancel_more_than_two_hours_has_no_charge(self):
        order = self._make_order(
            pickup_delta_hours=4,
        )

        response = self._post_cancel(order)

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()

        self.assertEqual(order.status, "canceled")

        self.assertFalse(response.data["late_fee"])
        self.assertEqual(
            response.data["late_fee_amount"],
            0,
        )
        self.assertIsNone(
            response.data["customer_charge_id"]
        )

        self.assertFalse(
            CustomerCharge.objects.filter(
                order=order,
            ).exists()
        )

    def test_cancel_less_than_two_hours_creates_charge(self):
        order = self._make_order(
            pickup_delta_hours=1,
        )

        original_total = order.total_client_ttc

        response = self._post_cancel(order)

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()

        self.assertEqual(order.status, "canceled")
        self.assertEqual(
            order.total_client_ttc,
            original_total,
        )

        self.assertTrue(response.data["late_fee"])
        self.assertEqual(
            response.data["late_fee_amount"],
            1000,
        )
        self.assertEqual(
            response.data["customer_charge_status"],
            CustomerCharge.Status.DUE,
        )

        charges = CustomerCharge.objects.filter(
            order=order,
            charge_type=(
                CustomerCharge.ChargeType.LATE_CANCELLATION
            ),
        )

        self.assertEqual(charges.count(), 1)

        charge = charges.get()

        self.assertEqual(
            charge.customer,
            self.customer,
        )
        self.assertEqual(
            charge.amount,
            Decimal("1000.00"),
        )
        self.assertEqual(
            charge.status,
            CustomerCharge.Status.DUE,
        )
        self.assertEqual(
            charge.idempotency_key,
            f"late_cancellation:order:{order.id}",
        )

    def test_late_cancel_does_not_require_customer_wallet(self):
        order = self._make_order(
            pickup_delta_hours=1,
        )

        self.assertFalse(
            Wallet.objects.filter(
                owner_type="customer",
                customer=self.customer,
            ).exists()
        )

        response = self._post_cancel(order)

        self.assertEqual(response.status_code, 200)

        order.refresh_from_db()

        self.assertEqual(order.status, "canceled")

        self.assertEqual(
            CustomerCharge.objects.filter(
                order=order,
                status=CustomerCharge.Status.DUE,
            ).count(),
            1,
        )

        self.assertFalse(
            Wallet.objects.filter(
                owner_type="customer",
                customer=self.customer,
            ).exists()
        )

    def test_second_cancel_does_not_duplicate_charge(self):
        order = self._make_order(
            pickup_delta_hours=1,
        )

        first = self._post_cancel(order)

        self.assertEqual(first.status_code, 200)

        second = self._post_cancel(order)

        self.assertEqual(second.status_code, 400)

        self.assertEqual(
            CustomerCharge.objects.filter(
                order=order,
                charge_type=(
                    CustomerCharge.ChargeType.LATE_CANCELLATION
                ),
            ).count(),
            1,
        )

    def test_charge_failure_rolls_back_order_cancellation(self):
        order = self._make_order(
            pickup_delta_hours=1,
        )

        with patch(
            "payments.services.apply_customer_charge",
            side_effect=RuntimeError(
                "TEST A7 — panne création créance"
            ),
        ):
            response = self._post_cancel(order)

        self.assertEqual(response.status_code, 400)

        order.refresh_from_db()

        self.assertEqual(
            order.status,
            "pending",
            "La commande doit revenir à son état initial "
            "si la création de créance échoue.",
        )

        self.assertFalse(
            CustomerCharge.objects.filter(
                order=order,
            ).exists()
        )

    def test_pickup_time_already_passed_is_late_cancellation(self):
        """
        Contrat actuel :
        une collecte planifiée déjà dépassée est également considérée
        comme une annulation tardive.
        """
        order = self._make_order(
            pickup_delta_hours=-1,
        )

        response = self._post_cancel(order)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["late_fee"])

        self.assertEqual(
            CustomerCharge.objects.filter(
                order=order,
                charge_type=(
                    CustomerCharge.ChargeType.LATE_CANCELLATION
                ),
            ).count(),
            1,
        )
