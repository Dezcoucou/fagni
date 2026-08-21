from decimal import Decimal

from django.test import TestCase

from orders.models import Customer, Order
from payments.models import CustomerCharge
from payments.services import apply_customer_charge


class ApplyCustomerChargeTests(TestCase):

    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client Charge A7",
            phone="0700999901",
            address="Riviera 3",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status="pending",
            total_client_ttc=Decimal("5000.00"),
        )

    def test_late_cancellation_creates_due_charge(self):
        charge, created = apply_customer_charge(
            customer=self.customer,
            order=self.order,
            charge_type=(
                CustomerCharge.ChargeType.LATE_CANCELLATION
            ),
            amount=Decimal("1000"),
            reason="Annulation tardive test",
        )

        self.assertTrue(created)

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
            f"late_cancellation:order:{self.order.pk}",
        )

    def test_late_cancellation_is_idempotent(self):
        first, first_created = apply_customer_charge(
            customer=self.customer,
            order=self.order,
            charge_type=(
                CustomerCharge.ChargeType.LATE_CANCELLATION
            ),
            amount=Decimal("1000"),
            reason="Premier appel",
        )

        second, second_created = apply_customer_charge(
            customer=self.customer,
            order=self.order,
            charge_type=(
                CustomerCharge.ChargeType.LATE_CANCELLATION
            ),
            amount=Decimal("1000"),
            reason="Deuxième appel",
        )

        self.assertTrue(first_created)
        self.assertFalse(second_created)

        self.assertEqual(first.pk, second.pk)

        self.assertEqual(
            CustomerCharge.objects.filter(
                order=self.order,
                charge_type=(
                    CustomerCharge.ChargeType.LATE_CANCELLATION
                ),
            ).count(),
            1,
        )

    def test_wallet_is_not_required(self):
        charge, created = apply_customer_charge(
            customer=self.customer,
            order=self.order,
            charge_type=(
                CustomerCharge.ChargeType.LATE_CANCELLATION
            ),
            amount=Decimal("1000"),
        )

        self.assertTrue(created)
        self.assertEqual(
            charge.status,
            CustomerCharge.Status.DUE,
        )

    def test_zero_amount_is_refused(self):
        with self.assertRaises(ValueError):
            apply_customer_charge(
                customer=self.customer,
                order=self.order,
                charge_type=(
                    CustomerCharge.ChargeType.LATE_CANCELLATION
                ),
                amount=Decimal("0"),
            )

    def test_order_must_belong_to_customer(self):
        other_customer = Customer.objects.create(
            name="Autre client",
            phone="0700999902",
        )

        with self.assertRaises(ValueError):
            apply_customer_charge(
                customer=other_customer,
                order=self.order,
                charge_type=(
                    CustomerCharge.ChargeType.LATE_CANCELLATION
                ),
                amount=Decimal("1000"),
            )
