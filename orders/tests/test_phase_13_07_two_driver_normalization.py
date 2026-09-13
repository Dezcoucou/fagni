from django.test import TransactionTestCase

from orders.models import Order, DeliveryLeg, Customer
from orders.services import bootstrap_delivery_legs_for_order
from orders.views import normalize_order_legs
from partners.models import DeliveryPartner


class Phase1307TwoDriverNormalizationTest(TransactionTestCase):

    def test_two_different_drivers_must_preserve_pickup_assignment(self):
        customer = Customer.objects.create(
            name="Client 13.07",
            phone="010000001307",
            address="Abidjan",
        )

        pickup_driver = DeliveryPartner.objects.create(
            name="Pickup Driver 13.07",
            phone="0700001307",
            is_active=True,
        )

        return_driver = DeliveryPartner.objects.create(
            name="Return Driver 13.07",
            phone="0700001308",
            is_active=True,
        )

        order = Order.objects.create(
            customer=customer,
            status="pending",
            payment_status="unpaid",
            total_client_ttc=5000,
        )

        bootstrap_delivery_legs_for_order(order)

        pickup = DeliveryLeg.objects.get(
            order=order,
            leg_type="pickup",
        )

        pickup.driver = pickup_driver
        pickup.status = "assigned"
        pickup.save(update_fields=["driver", "status"])

        order.pickup_driver = pickup_driver
        order.delivery_partner = return_driver
        order.save(update_fields=["pickup_driver", "delivery_partner"])

        normalize_order_legs(order)

        pickup.refresh_from_db()

        print(
            f"RESULT: "
            f"pickup_driver={order.pickup_driver_id} "
            f"delivery_partner={order.delivery_partner_id} "
            f"leg_driver={pickup.driver_id} "
            f"status={pickup.status}"
        )

        self.assertEqual(pickup.driver_id, pickup_driver.id)
        self.assertEqual(
            pickup.status,
            "assigned",
            "Un livreur retour différent ne doit jamais annuler "
            "une collecte déjà assignée.",
        )
