from django.test import TransactionTestCase

from orders.models import Order, DeliveryLeg, Customer
from orders.services import bootstrap_delivery_legs_for_order
from orders.views import update_leg_status
from partners.models import DeliveryPartner


class Phase1315AcceptPreservesTwoDriversTest(TransactionTestCase):

    def test_accept_pickup_does_not_overwrite_return_driver(self):
        customer = Customer.objects.create(
            name="Client 13.15",
            phone="010000001315",
            address="Abidjan",
        )

        pickup_driver = DeliveryPartner.objects.create(
            name="Pickup Driver 13.15",
            phone="0700001315",
            is_active=True,
        )

        return_driver = DeliveryPartner.objects.create(
            name="Return Driver 13.15",
            phone="0700001316",
            is_active=True,
        )

        order = Order.objects.create(
            customer=customer,
            status="pending",
            payment_status="unpaid",
            total_client_ttc=5000,
            pickup_driver=pickup_driver,
            delivery_partner=return_driver,
        )

        from django.utils import timezone

        order.wash_complete_time = timezone.now()
        order.save(update_fields=["wash_complete_time"])

        bootstrap_delivery_legs_for_order(order)

        pickup = DeliveryLeg.objects.get(
            order=order,
            leg_type="pickup",
        )

        return_leg = DeliveryLeg.objects.get(
            order=order,
            leg_type="return",
        )

        pickup.driver = pickup_driver
        pickup.status = "pending"
        pickup.save(update_fields=["driver", "status"])

        return_leg.driver = return_driver
        return_leg.status = "pending"
        return_leg.save(update_fields=["driver", "status"])

        order.refresh_from_db()

        ok, message = update_leg_status(
            pickup,
            action="accept",
            user=None,
        )

        pickup.refresh_from_db()
        return_leg.refresh_from_db()

        print(
            f"RESULT: "
            f"ok={ok} "
            f"message={message} "
            f"pickup_driver={pickup.driver_id} "
            f"pickup_status={pickup.status} "
            f"return_driver={return_leg.driver_id} "
            f"return_status={return_leg.status}"
        )

        self.assertTrue(ok)
        self.assertEqual(pickup.driver_id, pickup_driver.id)
        self.assertEqual(pickup.status, "assigned")

        self.assertEqual(
            return_leg.driver_id,
            return_driver.id,
            "L'acceptation du pickup ne doit pas remplacer "
            "le livreur du return.",
        )
