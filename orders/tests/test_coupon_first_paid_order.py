from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from orders.models import Coupon, CouponUsage, Customer, Order
from orders.views import validate_and_get_coupon_discount


class CouponFirstPaidOrderTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client coupon",
            phone="0700998800",
            address="Riviera 3",
        )

        self.coupon = Coupon.objects.create(
            code="FAGNI30",
            description="Offre pilote",
            discount_type="percent",
            discount_value=Decimal("30"),
            first_order_only=True,
            max_uses_per_customer=1,
            max_total_uses=100,
            valid_from=timezone.now() - timedelta(days=1),
            valid_until=timezone.now() + timedelta(days=30),
            is_active=True,
        )

    def _make_order(
        self,
        *,
        payment_status="pending",
        total=Decimal("9000"),
    ):
        order = Order.objects.create(
            customer=self.customer,
            status="pending",
            payment_status=payment_status,
            amount_paid=total if payment_status == "paid" else Decimal("0"),
            total_client_ttc=total,
            pricing_mode="item",
            is_draft=False,
        )

        # Le modèle Order possède des synchronisations financières lors du
        # save(). Pour ce test ciblé du coupon, on verrouille explicitement
        # le snapshot financier attendu sans déclencher un nouveau recalcul.
        Order.objects.filter(pk=order.pk).update(
            total_client_ttc=total,
            total=total,
            delivery_fee=Decimal("2000"),
            service_fee=Decimal("500"),
            payment_status=payment_status,
            amount_paid=(
                total
                if payment_status == "paid"
                else Decimal("0")
            ),
        )

        order.refresh_from_db()
        return order

    @staticmethod
    def _prestation_total(order):
        return (order.total_client_ttc or 0) - (order.delivery_fee or 0) - (order.service_fee or 0)

    def test_previous_unpaid_order_does_not_block_first_order_coupon(self):
        self._make_order(payment_status="pending")
        current_order = self._make_order(payment_status="pending")

        discount, error, coupon = validate_and_get_coupon_discount(
            current_order.customer,
            self._prestation_total(current_order),
            "FAGNI30",
            exclude_order_pk=current_order.pk,
        )

        self.assertIsNone(error)
        self.assertEqual(coupon, self.coupon)
        self.assertEqual(discount, Decimal("1950"))

    def test_previous_paid_order_blocks_first_order_coupon(self):
        self._make_order(payment_status="paid")
        current_order = self._make_order(payment_status="pending")

        discount, error, coupon = validate_and_get_coupon_discount(
            current_order.customer,
            self._prestation_total(current_order),
            "FAGNI30",
            exclude_order_pk=current_order.pk,
        )

        self.assertEqual(discount, Decimal("0"))
        self.assertEqual(error, "reserve_premiere_commande")
        self.assertEqual(coupon, self.coupon)

    def test_previous_coupon_usage_blocks_second_use(self):
        previous_order = self._make_order(payment_status="pending")

        CouponUsage.objects.create(
            coupon=self.coupon,
            customer=self.customer,
            order=previous_order,
            discount_amount=Decimal("1950"),
        )

        current_order = self._make_order(payment_status="pending")

        discount, error, coupon = validate_and_get_coupon_discount(
            current_order.customer,
            self._prestation_total(current_order),
            "FAGNI30",
            exclude_order_pk=current_order.pk,
        )

        self.assertEqual(discount, Decimal("0"))
        self.assertEqual(error, "deja_utilise")
        self.assertEqual(coupon, self.coupon)

    def test_preview_does_not_exclude_current_order_when_none_given(self):
        # Apercu avant creation de commande : aucune order a exclure. S'assure
        # que exclude_order_pk=None (le defaut) ne casse pas la requete
        # d'exclusion et ne bloque pas un premier apercu legitime.
        discount, error, coupon = validate_and_get_coupon_discount(
            self.customer,
            Decimal("6500"),
            "FAGNI30",
        )

        self.assertIsNone(error)
        self.assertEqual(coupon, self.coupon)
        self.assertEqual(discount, Decimal("1950"))
