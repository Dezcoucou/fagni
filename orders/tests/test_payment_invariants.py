from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from orders.models import Customer, Order
from orders.views import apply_order_payment
from orders.presenters import build_order_finance_summary


class PaymentInvariantTests(TestCase):
    def setUp(self):
        self.c = Customer.objects.create(name="Test", phone="01020304", address="Test")

    def test_paid_forces_amount_paid_and_payment_date(self):
        """
        Reecrit pour utiliser apply_order_payment() - seul point d'entree
        legitime depuis l'ajout du garde-fou anti-fraude sur Order.save()
        (meme principe que WALLET_FRAUD_BLOCKED). L'invariant teste reste
        le meme : payer le total doit remplir amount_paid ET payment_date.
        """
        o = Order.objects.create(
            customer=self.c,
            status="pending",
            payment_status="unpaid",
            pricing_mode="bag",
            bag_size="medium",
            amount_paid=Decimal("0"),
        )

        try:
            o.update_financials(save=True)
        except Exception:
            import logging
            logging.getLogger("fagni.tests.orders.tests.test_payment_invariants").exception("Exception silencieuse (auto-log) - fichier=orders/tests/test_payment_invariants.py ligne=28")

        expected_total = build_order_finance_summary(o)["total_client_ttc"]

        with patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None):
            apply_order_payment(o, expected_total, channel="manual")

        o.refresh_from_db()
        self.assertEqual(o.payment_status, "paid")
        self.assertEqual(o.amount_paid, expected_total)
        self.assertIsNotNone(o.payment_date)

    def test_pending_clears_amount_and_date(self):
        o = Order.objects.create(
            customer=self.c,
            status="pending",
            payment_status="unpaid",
            pricing_mode="bag",
            bag_size="medium",
            amount_paid=Decimal("500"),
            payment_date=timezone.now(),
        )
        o.save()
        o.refresh_from_db()

        self.assertEqual(o.payment_status, "pending")
        self.assertEqual(o.amount_paid, Decimal("0"))
        self.assertIsNone(o.payment_date)

    def test_amount_paid_is_capped_to_total(self):
        """
        Reecrit pour utiliser apply_order_payment() - le plafonnement au
        total est desormais garanti par to_apply = min(add_amount, remaining)
        dans apply_order_payment(), jamais par une modification directe.
        """
        o = Order.objects.create(
            customer=self.c,
            status="pending",
            payment_status="partial",
            pricing_mode="bag",
            bag_size="medium",
            amount_paid=Decimal("5000"),
        )

        try:
            o.update_financials(save=True)
        except Exception:
            import logging
            logging.getLogger("fagni.tests.orders.tests.test_payment_invariants").exception("Exception silencieuse (auto-log) - fichier=orders/tests/test_payment_invariants.py ligne=73")

        expected_total = build_order_finance_summary(o)["total_client_ttc"]

        apply_order_payment(o, expected_total + Decimal("5000"), channel="manual")
        o.refresh_from_db()

        self.assertEqual(o.amount_paid, expected_total)

    def test_cannot_mark_paid_with_zero_total(self):
        o = Order.objects.create(
            customer=self.c,
            status="pending",
            payment_status="unpaid",
            pricing_mode="item",
            amount_paid=Decimal("0"),
        )

        try:
            o.update_financials(save=True)
        except Exception:
            import logging
            logging.getLogger("fagni.tests.orders.tests.test_payment_invariants").exception("Exception silencieuse (auto-log) - fichier=orders/tests/test_payment_invariants.py ligne=95")

        expected_total = build_order_finance_summary(o)["total_client_ttc"]
        self.assertEqual(expected_total, Decimal("0"))

        o.payment_status = "paid"
        with self.assertRaises(ValidationError):
            o.save()
