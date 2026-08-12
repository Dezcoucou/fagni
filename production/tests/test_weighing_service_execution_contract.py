from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from logistics.models import Mission
from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob, WeighingRecord
from production.services import record_weighing
from services.models import Service, ServiceCategory, ServiceExecution


class WeighingServiceExecutionContractTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="test-weighing-category",
            name="Test Weighing Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="test-weighing-service",
            category=self.category,
            name="Test Weighing Service",
            description="",
            is_active=True,
            primary_engine=Service.ENGINE_PICKUP_RETURN,
            requires_partner=True,
            requires_logistics=True,
            requires_weighing=True,
            requires_appointment=False,
            requires_quote=False,
            requires_asset=False,
            requires_otp=False,
            requires_signature=False,
            pricing_mode="fixed",
            default_sla_hours=24,
        )

        self.customer_a = Customer.objects.create(
            name="Client Weighing A",
            phone="0700003001",
        )

        self.customer_b = Customer.objects.create(
            name="Client Weighing B",
            phone="0700003002",
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

        self.execution_b = ServiceExecution.objects.create(
            order=self.order_b,
            service=self.service,
            execution_engine=self.service.primary_engine,
            status=ServiceExecution.STATUS_PENDING,
        )

        self.partner = LaundryPartner.objects.create(
            name="Blanchisserie Test Weighing",
        )

        self.mission_a = Mission.objects.create(
            code="MSN-WEIGH-A",
            order=self.order_a,
            service_execution=self.execution_a,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        self.partner_job_a = PartnerJob.objects.create(
            code="JOB-WEIGH-A",
            order=self.order_a,
            service_execution=self.execution_a,
            partner=self.partner,
        )

    def test_legacy_weighing_without_service_execution_is_allowed(self):
        record = WeighingRecord.objects.create(
            order=self.order_a,
            net_weight=Decimal("3.50"),
            weighing_stage="partner_reception",
        )

        self.assertIsNone(record.service_execution)

    def test_weighing_with_same_execution_is_allowed(self):
        record = record_weighing(
            order=self.order_a,
            service_execution=self.execution_a,
            mission=self.mission_a,
            partner_job=self.partner_job_a,
            net_weight=Decimal("3.50"),
            weighing_stage="partner_reception",
        )

        self.assertEqual(
            record.service_execution_id,
            self.execution_a.id,
        )
        self.assertEqual(
            record.order_id,
            self.execution_a.order_id,
        )

    def test_weighing_with_execution_from_different_order_is_rejected(self):
        record = WeighingRecord(
            order=self.order_b,
            service_execution=self.execution_a,
            net_weight=Decimal("3.50"),
            weighing_stage="partner_reception",
        )

        with self.assertRaises(ValidationError):
            record.save()

    def test_service_rejects_execution_from_different_order(self):
        with self.assertRaises(ValueError):
            record_weighing(
                order=self.order_b,
                service_execution=self.execution_a,
                net_weight=Decimal("3.50"),
                weighing_stage="partner_reception",
            )

    def test_service_rejects_partner_job_from_different_execution(self):
        partner_job_b = PartnerJob.objects.create(
            code="JOB-WEIGH-B",
            order=self.order_b,
            service_execution=self.execution_b,
            partner=self.partner,
        )

        with self.assertRaises(ValueError):
            record_weighing(
                order=self.order_a,
                service_execution=self.execution_a,
                partner_job=partner_job_b,
                net_weight=Decimal("3.50"),
                weighing_stage="partner_reception",
            )

    def test_service_rejects_mission_from_different_execution(self):
        mission_b = Mission.objects.create(
            code="MSN-WEIGH-B",
            order=self.order_b,
            service_execution=self.execution_b,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        with self.assertRaises(ValueError):
            record_weighing(
                order=self.order_a,
                service_execution=self.execution_a,
                mission=mission_b,
                net_weight=Decimal("3.50"),
                weighing_stage="partner_reception",
            )
