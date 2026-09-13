from unittest.mock import patch
import jwt
from django.conf import settings

from django.test import TransactionTestCase, override_settings
from django.db import transaction
from rest_framework.test import APIRequestFactory

from orders.models import Order, DeliveryLeg, Customer
from partners.models import DeliveryPartner
from orders import ops_api
from orders.services import bootstrap_delivery_legs_for_order


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
)
class Phase1297TraceAssignmentTest(TransactionTestCase):

    def test_trace_ops_assignment_status(self):
        factory = APIRequestFactory()

        customer = Customer.objects.create(
            name="Trace Client",
            phone="0100000097",
            address="Abidjan",
        )

        driver = DeliveryPartner.objects.create(
            name="Trace Driver",
            phone="0700000097",
            is_active=True,
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

        print(
            f"\nTRACE INITIAL: "
            f"leg_id={leg.id} "
            f"status={leg.status} "
            f"driver={leg.driver_id}"
        )

        original_save = DeliveryLeg.save

        def traced_save(self, *args, **kwargs):
            before = (
                DeliveryLeg.objects
                .filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
                if self.pk
                else None
            )

            print(
                f"TRACE SAVE BEFORE: "
                f"leg_id={self.pk} "
                f"db_status={before} "
                f"new_status={self.status} "
                f"driver={self.driver_id} "
                f"update_fields={kwargs.get('update_fields')}"
            )

            result = original_save(self, *args, **kwargs)

            after = (
                DeliveryLeg.objects
                .filter(pk=self.pk)
                .values_list("status", flat=True)
                .first()
            )

            print(
                f"TRACE SAVE AFTER: "
                f"leg_id={self.pk} "
                f"db_status={after} "
                f"instance_status={self.status}"
            )

            return result

        with patch.object(DeliveryLeg, "save", new=traced_save):

            token = jwt.encode(
                {"ops": True, "name": "Opérateur FAGNI"},
                settings.SECRET_KEY,
                algorithm="HS256",
            )

            request = factory.post(
                f"/api/ops/orders/{order.id}/assign-driver/",
                {"driver_id": driver.id},
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

            with transaction.atomic():
                response = ops_api.ops_assign_driver(
                    request,
                    order_id=order.id,
                )

                leg_inside = DeliveryLeg.objects.get(
                    order=order,
                    leg_type="pickup",
                )

                print(
                    f"TRACE INSIDE ATOMIC: "
                    f"status={leg_inside.status} "
                    f"driver={leg_inside.driver_id}"
                )

            leg_after_commit = DeliveryLeg.objects.get(
                order=order,
                leg_type="pickup",
            )

            print(
                f"TRACE AFTER COMMIT: "
                f"status={leg_after_commit.status} "
                f"driver={leg_after_commit.driver_id}"
            )

            print(
                f"TRACE ENDPOINT: "
                f"http={response.status_code}"
            )

        leg.refresh_from_db()

        print(
            f"TRACE FINAL: "
            f"leg_id={leg.id} "
            f"status={leg.status} "
            f"driver={leg.driver_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(leg.status, "assigned")
