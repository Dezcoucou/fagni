from django.test import TransactionTestCase

from orders.models import Order, DeliveryLeg
from orders.services import bootstrap_delivery_legs_for_order
from orders.views import normalize_order_legs
from orders.models import Customer
from partners.models import DeliveryPartner


class Phase1305DriverAuthorityTest(TransactionTestCase):

    def make_order(self):
        customer = Customer.objects.create(
            name="Client 13.05",
            phone="0100000013",
            address="Abidjan",
        )

        driver_a = DeliveryPartner.objects.create(
            name="Driver A 13.05",
            phone="0700000013",
        )

        driver_b = DeliveryPartner.objects.create(
            name="Driver B 13.05",
            phone="0700000014",
        )

        order = Order.objects.create(
            customer=customer,
            status="pending",
            payment_status="unpaid",
            total_client_ttc=5000,
        )

        bootstrap_delivery_legs_for_order(order)

        leg = DeliveryLeg.objects.get(
            order=order,
            leg_type="pickup",
        )

        leg.driver = driver_a
        leg.status = "assigned"
        leg.save(update_fields=["driver", "status"])

        return order, leg, driver_a, driver_b

    def test_A_pickup_driver_only(self):
        order, leg, driver_a, driver_b = self.make_order()

        order.pickup_driver = driver_a
        order.delivery_partner = None
        order.save(update_fields=["pickup_driver", "delivery_partner"])

        normalize_order_legs(order)

        leg.refresh_from_db()

        print(
            f"CASE A: "
            f"pickup_driver={order.pickup_driver_id} "
            f"delivery_partner={order.delivery_partner_id} "
            f"leg_driver={leg.driver_id} "
            f"status={leg.status}"
        )

        self.assertEqual(leg.status, "assigned")
        self.assertEqual(leg.driver_id, driver_a.id)

    def test_B_same_driver(self):
        order, leg, driver_a, driver_b = self.make_order()

        order.pickup_driver = driver_a
        order.delivery_partner = driver_a
        order.save(update_fields=["pickup_driver", "delivery_partner"])

        normalize_order_legs(order)

        leg.refresh_from_db()

        print(
            f"CASE B: "
            f"pickup_driver={order.pickup_driver_id} "
            f"delivery_partner={order.delivery_partner_id} "
            f"leg_driver={leg.driver_id} "
            f"status={leg.status}"
        )

        self.assertEqual(leg.status, "assigned")
        self.assertEqual(leg.driver_id, driver_a.id)

    def test_C_different_driver(self):
        order, leg, driver_a, driver_b = self.make_order()

        order.pickup_driver = driver_a
        order.delivery_partner = driver_b
        order.save(update_fields=["pickup_driver", "delivery_partner"])

        normalize_order_legs(order)

        leg.refresh_from_db()

        print(
            f"CASE C: "
            f"pickup_driver={order.pickup_driver_id} "
            f"delivery_partner={order.delivery_partner_id} "
            f"leg_driver={leg.driver_id} "
            f"status={leg.status}"
        )
