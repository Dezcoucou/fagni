from django.core.exceptions import ValidationError
from django.test import TestCase

from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob
from production.services import create_partner_job
from services.models import Service, ServiceCategory, ServiceExecution


class PartnerJobServiceExecutionContractTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="test-production-category",
            name="Test Production Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="test-production-service",
            category=self.category,
            name="Test Production Service",
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
            name="Client Production A",
            phone="0700002001",
        )

        self.customer_b = Customer.objects.create(
            name="Client Production B",
            phone="0700002002",
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

        self.partner = LaundryPartner.objects.create(
            name="Blanchisserie Test 3B7",
        )

    def test_legacy_partner_job_without_service_execution_is_allowed(self):
        job = PartnerJob.objects.create(
            code="JOB-LEGACY-3B7",
            order=self.order_a,
            partner=self.partner,
        )

        self.assertIsNone(job.service_execution)

    def test_partner_job_with_execution_from_same_order_is_allowed(self):
        job = create_partner_job(
            order=self.order_a,
            partner=self.partner,
            service_execution=self.execution_a,
        )

        self.assertEqual(
            job.service_execution_id,
            self.execution_a.id,
        )
        self.assertEqual(
            job.order_id,
            self.execution_a.order_id,
        )

    def test_partner_job_with_execution_from_different_order_is_rejected(self):
        job = PartnerJob(
            code="JOB-CROSS-3B7",
            order=self.order_b,
            partner=self.partner,
            service_execution=self.execution_a,
        )

        with self.assertRaises(ValidationError):
            job.save()

    def test_service_rejects_execution_from_different_order(self):
        with self.assertRaises(ValueError):
            create_partner_job(
                order=self.order_b,
                partner=self.partner,
                service_execution=self.execution_a,
            )
