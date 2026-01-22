from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.test import TestCase

from orders.models import Customer, Order, OrderItem, Payment


class PaymentGuardsTests(TestCase):
    def _make_order_with_total(self, phone="0700009999"):
        c, _ = Customer.objects.get_or_create(
            phone=phone,
            defaults={"name": "TEST", "address": "X", "latitude": 0, "longitude": 0},
        )
        o = Order.objects.create(customer=c)
        OrderItem.objects.create(order=o, designation="TEST PRESTATION", quantity=1, unit_price=Decimal("3000"))
        o.update_financials(save=True)
        o.refresh_from_db()
        self.assertGreater(Decimal(str(o.total_client_ttc or 0)), 0)
        return o

    def test_add_payment_refused_if_total_is_zero(self):
        c, _ = Customer.objects.get_or_create(
            phone="0700000109",
            defaults={"name": "T", "address": "X", "latitude": 0, "longitude": 0},
        )
        o = Order.objects.create(customer=c)  # total_client_ttc = 0
        with self.assertRaises(ValidationError):
            o.add_payment(amount=1000, channel="test", reference=f"TEST-{o.code}-P1", source="system", save=True)

    def test_payment_direct_refused_if_total_is_zero(self):
        c, _ = Customer.objects.get_or_create(
            phone="0700000110",
            defaults={"name": "T2", "address": "X", "latitude": 0, "longitude": 0},
        )
        o = Order.objects.create(customer=c)  # total_client_ttc = 0
        with self.assertRaises(ValidationError):
            Payment.objects.create(order=o, amount=1000, channel="test", reference="DIRECT", source="system")

    def test_add_payment_caps_overpayment_and_reaches_paid(self):
        o = self._make_order_with_total(phone="0700000111")

        o.add_payment(amount=1000, channel="test", reference=f"AP-{o.code}-1", source="system", save=True)
        o.refresh_from_db()
        self.assertEqual(o.payment_status, "partial")
        self.assertEqual(Decimal(str(o.amount_paid)), Decimal("1000"))
        self.assertGreater(Decimal(str(o.amount_remaining)), 0)

        # surpaiement volontaire -> doit être cappé au reste
        o.add_payment(amount=999999, channel="test", reference=f"AP-{o.code}-2", source="system", save=True)
        o.refresh_from_db()
        self.assertEqual(o.payment_status, "paid")
        self.assertEqual(Decimal(str(o.amount_remaining)), Decimal("0"))

        paid_sum = Payment.objects.filter(order=o).aggregate(s=models.Sum("amount")).get("s") or 0
        self.assertEqual(Decimal(str(paid_sum)), Decimal(str(o.total_client_ttc)))

    def test_payment_direct_refuses_overpayment_remaining(self):
        o = self._make_order_with_total(phone="0700000112")

        Payment.objects.create(order=o, amount=1000, channel="test", reference="D1", source="system")

        # reste dû = total - 1000 -> un gros montant doit être refusé
        with self.assertRaises(ValidationError):
            Payment.objects.create(order=o, amount=999999, channel="test", reference="D2", source="system")

    def test_payment_direct_triggers_order_resync(self):
        o = self._make_order_with_total(phone="0700000113")

        Payment.objects.create(order=o, amount=1000, channel="test", reference="DIR-1", source="system")
        o.refresh_from_db()
        self.assertEqual(o.payment_status, "partial")
        self.assertEqual(Decimal(str(o.amount_paid)), Decimal("1000"))

        remaining = Decimal(str(o.total_client_ttc)) - Decimal("1000")
        Payment.objects.create(order=o, amount=int(remaining), channel="test", reference="DIR-2", source="system")
        o.refresh_from_db()
        self.assertEqual(o.payment_status, "paid")
        self.assertEqual(Decimal(str(o.amount_remaining)), Decimal("0"))

    # =========================
    # ✅ NOUVEAUX TESTS (4)
    # =========================

    def test_add_payment_idempotent_same_reference(self):
        o = self._make_order_with_total(phone="0700000114")
        ref = f"IDEMP-{o.code}-1"

        p1 = o.add_payment(amount=1000, channel="test", reference=ref, source="system", save=True)
        p2 = o.add_payment(amount=1000, channel="test", reference=ref, source="system", save=True)

        self.assertIsNotNone(p1)
        self.assertIsNotNone(p2)
        self.assertEqual(p1.pk, p2.pk)
        self.assertEqual(Payment.objects.filter(order=o, reference=ref).count(), 1)

    def test_payment_get_or_create_idempotent_same_reference(self):
        """
        On simule le comportement "PSP idempotent": order+reference => un seul Payment.
        (Notre modèle Payment n'impose pas un unique DB, donc on utilise get_or_create ici.)
        """
        o = self._make_order_with_total(phone="0700000115")
        ref = "PSP-REF-1"

        p1, created1 = Payment.objects.get_or_create(
            order=o,
            reference=ref,
            defaults={"amount": 1000, "channel": "test", "source": "system"},
        )
        p2, created2 = Payment.objects.get_or_create(
            order=o,
            reference=ref,
            defaults={"amount": 1000, "channel": "test", "source": "system"},
        )

        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(p1.pk, p2.pk)
        self.assertEqual(Payment.objects.filter(order=o, reference=ref).count(), 1)

    def test_payment_direct_refused_when_already_sold(self):
        o = self._make_order_with_total(phone="0700000116")

        # on solde via add_payment (cap au total)
        o.add_payment(amount=999999, channel="test", reference=f"SOLD-{o.code}-1", source="system", save=True)
        o.refresh_from_db()
        self.assertEqual(o.payment_status, "paid")
        self.assertEqual(Decimal(str(o.amount_remaining)), Decimal("0"))

        # direct Payment doit refuser (commande déjà soldée)
        with self.assertRaises(ValidationError):
            Payment.objects.create(order=o, amount=1, channel="test", reference="DIRECT-OVER", source="system")

    def test_add_payment_returns_none_when_already_sold(self):
        o = self._make_order_with_total(phone="0700000117")

        # solde
        o.add_payment(amount=999999, channel="test", reference=f"SOLD-{o.code}-1", source="system", save=True)
        o.refresh_from_db()
        self.assertEqual(o.payment_status, "paid")

        count_before = Payment.objects.filter(order=o).count()
        res = o.add_payment(amount=1000, channel="test", reference=f"SOLD-{o.code}-2", source="system", save=True)
        count_after = Payment.objects.filter(order=o).count()

        self.assertIsNone(res)
        self.assertEqual(count_after, count_before)
