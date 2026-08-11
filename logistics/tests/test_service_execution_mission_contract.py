from django.core.exceptions import ValidationError
from django.test import TestCase

from logistics.models import Mission
from orders.models import Customer, Order
from services.models import Service, ServiceCategory, ServiceExecution


class MissionServiceExecutionContractTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="test-category",
            name="Test Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="test-service",
            category=self.category,
            name="Test Service",
            description="",
            is_active=True,
            primary_engine=Service.ENGINE_PICKUP_RETURN,
            requires_partner=True,
            requires_logistics=True,
            requires_weighing=False,
            requires_appointment=False,
            requires_quote=False,
            requires_asset=False,
            requires_otp=False,
            requires_signature=False,
            pricing_mode="fixed",
            default_sla_hours=24,
        )

        self.customer_a = Customer.objects.create(
            name="Client Test A",
            phone="0700001001",
        )

        self.customer_b = Customer.objects.create(
            name="Client Test B",
            phone="0700001002",
        )

        self.order_a = Order.objects.create(
            customer=self.customer_a,
        )

        self.order_b = Order.objects.create(
            customer=self.customer_b,
        )

        self.execution_a = ServiceExecution.objects.create(
            order=self.order_a,
            service=self.service,
            execution_engine=self.service.primary_engine,
            status=ServiceExecution.STATUS_PENDING,
        )

    def test_legacy_mission_without_service_execution_is_allowed(self):
        mission = Mission.objects.create(
            code="MSN-LEGACY-001",
            order=self.order_a,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        self.assertIsNone(mission.service_execution)

    def test_mission_with_execution_from_same_order_is_allowed(self):
        mission = Mission.objects.create(
            code="MSN-SAME-001",
            order=self.order_a,
            service_execution=self.execution_a,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        self.assertEqual(mission.service_execution_id, self.execution_a.id)
        self.assertEqual(mission.order_id, self.execution_a.order_id)

    def test_mission_with_execution_from_different_order_is_rejected(self):
        mission = Mission(
            code="MSN-CROSS-001",
            order=self.order_b,
            service_execution=self.execution_a,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        with self.assertRaises(ValidationError):
            mission.save()
