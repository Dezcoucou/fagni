from decimal import Decimal

from django.test import TestCase

from orders.models import Customer, Order, DeliveryLeg
from partners.models import DeliveryPartner


class ExpressP03Tests(TestCase):

    def _create_customer_and_driver(self):
        """Helper pour créer un customer et un driver valides."""
        customer = Customer.objects.create(
            name="Test Customer",
            phone="0700000999"
        )
        driver = DeliveryPartner.objects.create(
            name="Test Driver",
            phone="0700000888"
        )
        return customer, driver

    def test_express_fee_not_diluted_by_distance(self):
        """
        P0.3 :
        Le supplément express est ajouté équitablement aux jambes actives,
        indépendamment de leur distance.
        """
        customer, driver = self._create_customer_and_driver()

        order = Order.objects.create(
            customer=customer,
            delivery_mode="express",
            express_extra_fee=Decimal("1000"),
            delivery_fee=Decimal("3000"),
            amount_driver_partner=Decimal("2000"),
            logistic_margin=Decimal("1000"),
        )

        leg1 = DeliveryLeg.objects.create(
            order=order,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            distance_km=Decimal("10"),
        )

        leg2 = DeliveryLeg.objects.create(
            order=order,
            driver=driver,
            leg_type="return",
            status="assigned",
            distance_km=Decimal("50"),
        )

        order.recompute_logistics_from_legs(
            save_legs=True,
            save_order=True,
        )

        leg1.refresh_from_db()
        leg2.refresh_from_db()

        self.assertEqual(
            leg1.driver_amount + leg2.driver_amount,
            Decimal("3000.00"),
        )

        # Chaque jambe active reçoit exactement 500 FCFA d'express au-dessus de sa part normale.
        # Pool 2000, distances 10/50 -> 333 + 1667 = 2000 (arrondi FCFA)
        # Express 1000 / 2 = 500 par jambe
        # leg1 = 333 + 500 = 833
        # leg2 = 1667 + 500 = 2167
        self.assertEqual(leg1.driver_amount, Decimal("833.00"))
        self.assertEqual(leg2.driver_amount, Decimal("2167.00"))
        self.assertEqual(leg2.driver_amount - leg1.driver_amount, Decimal("1334.00"))

    def test_express_fee_excludes_canceled_legs(self):
        """
        P0.3 :
        Une jambe canceled ne reçoit aucune part de l'express.
        """
        customer, driver = self._create_customer_and_driver()

        order = Order.objects.create(
            customer=customer,
            delivery_mode="express",
            express_extra_fee=Decimal("1000"),
            delivery_fee=Decimal("2000"),
            amount_driver_partner=Decimal("1500"),
            logistic_margin=Decimal("500"),
        )

        leg1 = DeliveryLeg.objects.create(
            order=order,
            driver=driver,
            leg_type="pickup",
            status="done",
            distance_km=Decimal("10"),
        )

        leg2 = DeliveryLeg.objects.create(
            order=order,
            driver=driver,
            leg_type="return",
            status="canceled",
            distance_km=Decimal("50"),
        )

        order.recompute_logistics_from_legs(
            save_legs=True,
            save_order=True,
        )

        leg1.refresh_from_db()
        leg2.refresh_from_db()

        self.assertEqual(leg2.driver_amount, Decimal("0.00"))
        # L'unique jambe active reçoit l'intégralité du supplément express.
        self.assertEqual(leg1.driver_amount, Decimal("2500.00"))
        self.assertEqual(leg1.driver_amount + leg2.driver_amount, Decimal("2500.00"))

    def test_express_fee_respects_payout_lock(self):
        """
        P0.3 + P0.1/P0.2 :
        un payout existant ne doit jamais être diminué.
        """
        customer, driver = self._create_customer_and_driver()

        order = Order.objects.create(
            customer=customer,
            delivery_mode="express",
            express_extra_fee=Decimal("500"),
            delivery_fee=Decimal("2000"),
            amount_driver_partner=Decimal("1500"),
            logistic_margin=Decimal("500"),
        )

        leg = DeliveryLeg.objects.create(
            order=order,
            driver=driver,
            leg_type="pickup",
            status="done",
            distance_km=Decimal("10"),
            driver_amount=Decimal("1500"),
        )

        order.delivery_fee = Decimal("2500")
        order.save(update_fields=["delivery_fee"])

        order.recompute_logistics_from_legs(
            save_legs=True,
            save_order=True,
        )

        leg.refresh_from_db()

        self.assertGreaterEqual(
            leg.driver_amount,
            Decimal("1500.00"),
        )
