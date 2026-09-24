from django.test import TestCase

from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob
from production.services import ensure_partner_job_for_pressing
from services.models import Service, ServiceCategory, ServiceExecution


class PartnerJobIdempotencyTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="partnerjob-idempotency-category",
            name="PartnerJob Idempotency Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="pressing_bag",
            category=self.category,
            name="Pressing Bag Idempotency Service",
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
            pricing_mode="bag",
            default_sla_hours=48,
        )

        self.customer = Customer.objects.create(
            name="Client PartnerJob Idempotency",
            phone="0700009301",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

        self.execution = ServiceExecution.objects.create(
            order=self.order,
            service=self.service,
            execution_engine=self.service.primary_engine,
            status=ServiceExecution.STATUS_PENDING,
        )

        self.partner = LaundryPartner.objects.create(
            name="Partner Idempotency",
        )

    def test_repeated_calls_reuse_same_partner_job(self):
        first_job = ensure_partner_job_for_pressing(
            order=self.order,
            partner=self.partner,
            service_execution=self.execution,
            notes="Première affectation",
        )

        second_job = ensure_partner_job_for_pressing(
            order=self.order,
            partner=self.partner,
            service_execution=self.execution,
            notes="Deuxième appel",
        )

        self.assertEqual(first_job.id, second_job.id)

        self.assertEqual(
            PartnerJob.objects.filter(
                order=self.order,
                partner=self.partner,
                service_execution=self.execution,
            ).count(),
            1,
        )

        first_job.refresh_from_db()

        self.assertEqual(
            first_job.status,
            "awaiting_reception",
        )

        self.assertIsNone(first_job.received_at)
        self.assertIsNone(first_job.processing_started_at)
        self.assertIsNone(first_job.ready_at)
        self.assertIsNone(first_job.handed_over_at)
