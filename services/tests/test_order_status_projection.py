from django.test import TestCase

from orders.models import (
    Customer,
    DeliveryLeg,
    Order,
    sync_order_status_from_legs,
)
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import (
    create_service_execution,
    schedule_service_execution,
    start_service_execution,
    complete_service_execution,
)


class OrderStatusProjectionTests(TestCase):
    """
    Contrat cible V2 :

    ServiceExecution = source de vérité de l'exécution métier.
    Order.status = projection agrégée des ServiceExecution.

    Ces tests sont volontairement introduits avant
    l'implémentation de la projection.
    """

    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="projection-category",
            name="Projection Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Projection",
            phone="0700099901",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

    def create_service(self, code):
        return Service.objects.create(
            code=code,
            category=self.category,
            name=code,
            description="",
            is_active=True,
            primary_engine=Service.ENGINE_PICKUP_RETURN,
            requires_partner=False,
            requires_logistics=False,
            requires_weighing=False,
            requires_appointment=False,
            requires_quote=False,
            requires_asset=False,
            requires_otp=False,
            requires_signature=False,
            pricing_mode="fixed",
            default_sla_hours=24,
        )

    def create_execution(self, code):
        service = self.create_service(code)

        return create_service_execution(
            order=self.order,
            service=service,
        )

    def start_execution(self, execution):
        schedule_service_execution(
            service_execution=execution,
        )
        start_service_execution(
            service_execution=execution,
        )

    def test_starting_execution_projects_order_in_progress(self):
        execution = self.create_execution("projection-start")

        self.start_execution(execution)

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.status,
            "in_progress",
        )

    def test_single_completed_execution_projects_order_done(self):
        execution = self.create_execution("projection-single")

        self.start_execution(execution)

        complete_service_execution(
            service_execution=execution,
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.status,
            "done",
        )

    def test_one_completed_execution_does_not_finish_multiservice_order(self):
        first = self.create_execution("projection-multi-first")
        second = self.create_execution("projection-multi-second")

        self.start_execution(first)
        self.start_execution(second)

        complete_service_execution(
            service_execution=first,
        )

        self.order.refresh_from_db()

        self.assertEqual(
            first.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertNotEqual(
            second.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertNotEqual(
            self.order.status,
            "done",
        )

    def test_all_completed_executions_project_order_done(self):
        first = self.create_execution("projection-all-first")
        second = self.create_execution("projection-all-second")

        self.start_execution(first)
        self.start_execution(second)

        complete_service_execution(
            service_execution=first,
        )

        self.order.refresh_from_db()

        self.assertNotEqual(
            self.order.status,
            "done",
        )

        complete_service_execution(
            service_execution=second,
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.status,
            "done",
        )

    def test_scheduled_execution_keeps_order_pending(self):
        execution = self.create_execution("projection-scheduled")

        schedule_service_execution(
            service_execution=execution,
        )

        self.order.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_SCHEDULED,
        )
        self.assertEqual(
            self.order.status,
            "pending",
        )

    def test_delivery_legs_cannot_override_v2_order_status(self):
        execution = self.create_execution("projection-v2-authority")

        self.start_execution(execution)

        pickup = DeliveryLeg.objects.create(
            order=self.order,
            leg_type="pickup",
            status="pending",
        )
        return_leg = DeliveryLeg.objects.create(
            order=self.order,
            leg_type="return",
            status="pending",
        )

        DeliveryLeg.objects.filter(
            pk__in=[pickup.pk, return_leg.pk],
        ).update(
            status="done",
        )

        projected = sync_order_status_from_legs(
            self.order,
            save=True,
        )

        self.order.refresh_from_db()

        self.assertEqual(
            projected,
            "in_progress",
        )
        self.assertEqual(
            self.order.status,
            "in_progress",
        )

    def test_legacy_order_without_execution_still_uses_delivery_legs(self):
        legacy_customer = Customer.objects.create(
            name="Client Legacy Projection",
            phone="0700099902",
        )

        legacy_order = Order.objects.create(
            customer=legacy_customer,
            status="pending",
        )

        pickup = DeliveryLeg.objects.create(
            order=legacy_order,
            leg_type="pickup",
            status="pending",
        )
        return_leg = DeliveryLeg.objects.create(
            order=legacy_order,
            leg_type="return",
            status="pending",
        )

        DeliveryLeg.objects.filter(
            pk__in=[pickup.pk, return_leg.pk],
        ).update(
            status="done",
        )

        projected = sync_order_status_from_legs(
            legacy_order,
            save=True,
        )

        legacy_order.refresh_from_db()

        self.assertEqual(
            projected,
            "done",
        )
        self.assertEqual(
            legacy_order.status,
            "done",
        )
