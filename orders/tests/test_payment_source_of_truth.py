from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase

from orders.models import Customer, Order, OrderItem, Payment
from orders.views import apply_order_payment
from partners.models import LaundryPartner


class PaymentSourceOfTruthTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client paiement canonique",
            phone="0700998800",
            address="Riviera 3",
        )

        self.laundry = (
            LaundryPartner.objects.filter(is_active=True).first()
            or LaundryPartner.objects.create(
                name="Pressing test paiement",
                address="Riviera 3",
                latitude=Decimal("5.360000"),
                longitude=Decimal("-3.950000"),
                is_active=True,
            )
        )

    def make_order(self, *, status="pending"):
        order = Order.objects.create(
            customer=self.customer,
            laundry_partner=self.laundry,
            status=status,
            pricing_mode="item",
        )

        OrderItem.objects.create(
            order=order,
            designation="Chemise",
            quantity=2,
            unit_price=Decimal("3000"),
            total=Decimal("6000"),
        )

        order.update_financials(save=True)
        order.refresh_from_db()

        self.assertGreater(
            Decimal(str(order.total_client_ttc or 0)),
            Decimal("0"),
        )
        return order

    @patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None)
    def test_apply_order_payment_cree_un_payment_canonique(self, _mock_distribute):
        order = self.make_order()
        amount = Decimal("1000")

        result = apply_order_payment(
            order,
            amount,
            channel="manual",
            reference="CANONIQUE-001",
            note="Test source de vérité",
        )

        payment = Payment.objects.get(
            order=order,
            reference="CANONIQUE-001",
        )

        order.refresh_from_db()

        self.assertEqual(payment.amount, amount)
        self.assertEqual(result["applied"], amount)
        self.assertEqual(order.amount_paid, amount)

        payment_sum = (
            Payment.objects
            .filter(order=order)
            .aggregate(total=Sum("amount"))
            .get("total")
            or Decimal("0")
        )
        self.assertEqual(order.amount_paid, payment_sum)

    @patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None)
    def test_rejouer_meme_reference_et_meme_montant_est_neutre(self, _mock_distribute):
        order = self.make_order()

        first = apply_order_payment(
            order,
            Decimal("1000"),
            channel="wave_webhook",
            reference="WAVE-IDEMPOTENT-001",
        )
        second = apply_order_payment(
            order,
            Decimal("1000"),
            channel="wave_webhook",
            reference="WAVE-IDEMPOTENT-001",
        )

        order.refresh_from_db()

        self.assertEqual(
            Payment.objects.filter(
                order=order,
                reference="WAVE-IDEMPOTENT-001",
            ).count(),
            1,
        )
        self.assertEqual(order.amount_paid, Decimal("1000"))
        self.assertEqual(first["applied"], Decimal("1000"))
        self.assertEqual(second["applied"], Decimal("0"))
        self.assertTrue(second["already_applied"])

    @patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None)
    def test_meme_reference_avec_montant_different_est_refusee(self, _mock_distribute):
        order = self.make_order()

        apply_order_payment(
            order,
            Decimal("1000"),
            channel="wave_webhook",
            reference="WAVE-CONFLIT-001",
        )

        with self.assertRaises(ValidationError):
            apply_order_payment(
                order,
                Decimal("1500"),
                channel="wave_webhook",
                reference="WAVE-CONFLIT-001",
            )

        payment = Payment.objects.get(
            order=order,
            reference="WAVE-CONFLIT-001",
        )

        order.refresh_from_db()

        self.assertEqual(payment.amount, Decimal("1000"))
        self.assertEqual(order.amount_paid, Decimal("1000"))

    def test_commande_annulee_ne_peut_pas_recevoir_de_paiement(self):
        order = self.make_order(status="canceled")

        with self.assertRaises(ValidationError):
            apply_order_payment(
                order,
                Decimal("1000"),
                channel="manual",
                reference="CANCELED-001",
            )

        self.assertFalse(Payment.objects.filter(order=order).exists())

        order.refresh_from_db()
        self.assertEqual(order.amount_paid, Decimal("0"))

    @patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None)
    def test_somme_payment_reste_source_de_verite_apres_resynchronisation(
        self,
        _mock_distribute,
    ):
        order = self.make_order()

        apply_order_payment(
            order,
            Decimal("1000"),
            channel="manual",
            reference="RESYNC-001",
        )

        order.refresh_from_db()
        order.sync_payment_status_from_payments(save=True)
        order.refresh_from_db()

        payment_sum = (
            Payment.objects
            .filter(order=order)
            .aggregate(total=Sum("amount"))
            .get("total")
            or Decimal("0")
        )

        self.assertEqual(order.amount_paid, payment_sum)
        self.assertEqual(order.payment_status, "partial")
