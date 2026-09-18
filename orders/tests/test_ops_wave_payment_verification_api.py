from decimal import Decimal
from unittest.mock import patch

import jwt
from django.conf import settings
from django.test import TestCase
from rest_framework.test import APIClient

from orders.models import Customer, Order, Payment


class OpsWavePaymentVerificationAPITests(TestCase):

    def setUp(self):
        self.client = APIClient()

        self.customer = Customer.objects.create(
            name="Client Wave Test",
            phone="0700000000",
            address="Abidjan",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status="pending",
            payment_status="declared",
            payment_verification_status="pending_review",
            payment_declared_channel="wave",
            payment_declared_reference="WAVE-DECLARE-001",
            pricing_mode="bag",
            bag_size="medium",
            amount_paid=Decimal("0"),
        )

        try:
            self.order.update_financials(save=True)
        except Exception:
            pass

        self.order.refresh_from_db()

        token = jwt.encode(
            {
                "ops": True,
                "name": "Opérateur FAGNI",
            },
            settings.SECRET_KEY,
            algorithm="HS256",
        )

        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {token}"
        )

    def test_requires_human_confirmation(self):
        response = self.client.post(
            f"/api/ops/orders/{self.order.id}/confirm-declared-wave/",
            {
                "verified_wave_reference": "WAVE-VERIFIED-001",
                "wave_human_verified": "false",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["error"],
            "human_confirmation_required",
        )

        self.order.refresh_from_db()
        self.assertNotEqual(self.order.payment_status, "paid")
        self.assertEqual(
            Payment.objects.filter(order=self.order).count(),
            0,
        )

    def test_requires_verified_reference(self):
        response = self.client.post(
            f"/api/ops/orders/{self.order.id}/confirm-declared-wave/",
            {
                "verified_wave_reference": "",
                "wave_human_verified": "true",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["error"],
            "verified_wave_reference_required",
        )

        self.order.refresh_from_db()
        self.assertNotEqual(self.order.payment_status, "paid")
        self.assertEqual(
            Payment.objects.filter(order=self.order).count(),
            0,
        )

    def test_duplicate_confirmation_does_not_create_second_payment(self):
        payload = {
            "verified_wave_reference": "WAVE-VERIFIED-IDEMP-001",
            "wave_human_verified": "true",
        }

        first = self.client.post(
            f"/api/ops/orders/{self.order.id}/confirm-declared-wave/",
            payload,
            format="json",
        )

        self.assertEqual(first.status_code, 200)

        payment_count = Payment.objects.filter(
            order=self.order,
            channel="wave_manual_verified",
        ).count()

        self.assertEqual(payment_count, 1)

        second = self.client.post(
            f"/api/ops/orders/{self.order.id}/confirm-declared-wave/",
            payload,
            format="json",
        )

        self.assertEqual(second.status_code, 400)

        payment_count_after = Payment.objects.filter(
            order=self.order,
            channel="wave_manual_verified",
        ).count()

        self.assertEqual(payment_count_after, 1)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")


    @patch("orders.models.log_event")
    def test_confirms_wave_payment_and_creates_payment(
        self,
        mock_log_event,
    ):
        response = self.client.post(
            f"/api/ops/orders/{self.order.id}/confirm-declared-wave/",
            {
                "verified_wave_reference": "WAVE-VERIFIED-001",
                "wave_human_verified": "true",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["success"])
        self.assertEqual(response.data["payment_status"], "paid")
        self.assertEqual(
            response.data["payment_verification_status"],
            "verified",
        )
        self.assertEqual(
            response.data["verified_wave_reference"],
            "WAVE-VERIFIED-001",
        )

        self.order.refresh_from_db()

        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(
            self.order.payment_verification_status,
            "verified",
        )
        self.assertEqual(
            self.order.payment_method,
            "wave",
        )
        self.assertIsNotNone(self.order.payment_date)
        self.assertIsNotNone(self.order.payment_verified_at)

        payments = Payment.objects.filter(
            order=self.order,
            channel="wave_manual_verified",
        )

        self.assertEqual(payments.count(), 1)

        payment = payments.first()

        self.assertEqual(
            payment.reference,
            "WAVE-VERIFIED-001",
        )
        self.assertEqual(
            payment.amount,
            self.order.amount_paid,
        )
        self.assertEqual(
            self.order.amount_paid,
            Decimal(
                str(
                    self.order.total_client_ttc
                    or self.order.total
                    or 0
                )
            ),
        )

        mock_log_event.assert_called_once()
