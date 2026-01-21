from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from orders.models import Customer, Order


class PaymentInvariantTests(TestCase):
    def setUp(self):
        self.c = Customer.objects.create(name="Test", phone="01020304", address="Test")

    def test_paid_forces_amount_paid_and_payment_date(self):
        o = Order.objects.create(
            customer=self.c,
            status="pending",
            payment_status="unpaid",
            total_client_ttc=Decimal("1000"),
            amount_paid=Decimal("0"),
        )

        with patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None):
            o.payment_status = "paid"
            o.amount_paid = Decimal("0")          # incohérent
            o.payment_date = None                 # incohérent
            o.save()

        o.refresh_from_db()
        self.assertEqual(o.payment_status, "paid")
        self.assertEqual(o.amount_paid, Decimal("1000"))
        self.assertIsNotNone(o.payment_date)

    def test_unpaid_clears_amount_and_date(self):
        o = Order.objects.create(
            customer=self.c,
            status="pending",
            payment_status="unpaid",
            total_client_ttc=Decimal("1000"),
            amount_paid=Decimal("500"),
            payment_date=timezone.now(),
        )
        o.save()
        o.refresh_from_db()

        self.assertEqual(o.payment_status, "unpaid")
        self.assertEqual(o.amount_paid, Decimal("0"))
        self.assertIsNone(o.payment_date)

    def test_amount_paid_is_capped_to_total(self):
        o = Order.objects.create(
            customer=self.c,
            status="pending",
            payment_status="partial",
            total_client_ttc=Decimal("1000"),
            amount_paid=Decimal("5000"),
        )
        o.save()
        o.refresh_from_db()

        self.assertEqual(o.amount_paid, Decimal("1000"))


    def test_cannot_mark_paid_with_zero_total(self):
        o = Order.objects.create(
            customer=self.c,
            status="pending",
            payment_status="unpaid",
            total_client_ttc=Decimal("0"),
            amount_paid=Decimal("0"),
        )

        o.payment_status = "paid"
        with self.assertRaises(ValidationError):
            o.save()
