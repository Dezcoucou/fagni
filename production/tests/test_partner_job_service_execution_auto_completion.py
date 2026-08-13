from django.test import TestCase

from logistics.models import Mission
from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob
from production.services import handover_partner_job
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import (
    cancel_service_execution,
    create_service_execution,
    schedule_service_execution,
    start_service_execution,
)


class PartnerJobServiceExecutionAutoCompletionTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="partnerjob-auto-completion-category",
            name="PartnerJob Auto Completion Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client PartnerJob Auto Completion",
            phone="0700009201",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

        self.partner = LaundryPartner.objects.create(
            name="Partner Auto Completion",
        )

    def create_service(self, **overrides):
        values = {
            "code": "partnerjob-auto-completion-service",
            "category": self.category,
            "name": "PartnerJob Auto Completion Service",
            "description": "",
            "is_active": True,
            "primary_engine": Service.ENGINE_PICKUP_RETURN,
            "requires_partner": True,
            "requires_logistics": False,
            "requires_weighing": False,
            "requires_appointment": False,
            "requires_quote": False,
            "requires_asset": False,
            "requires_otp": False,
            "requires_signature": False,
            "pricing_mode": "fixed",
            "default_sla_hours": 24,
        }
        values.update(overrides)
        return Service.objects.create(**values)

    def create_execution(
        self,
        service,
        status=ServiceExecution.STATUS_IN_PROGRESS,
    ):
        execution = create_service_execution(
            order=self.order,
            service=service,
        )

        if status == ServiceExecution.STATUS_PENDING:
            return execution

        if status == ServiceExecution.STATUS_CANCELED:
            return cancel_service_execution(
                service_execution=execution,
            )

        schedule_service_execution(
            service_execution=execution,
        )

        if status == ServiceExecution.STATUS_SCHEDULED:
            return execution

        start_service_execution(
            service_execution=execution,
        )

        if status == ServiceExecution.STATUS_IN_PROGRESS:
            return execution

        raise ValueError(
            "Statut ServiceExecution de test non supporté : "
            f"{status}."
        )

    def create_partner_job(
        self,
        *,
        service_execution=None,
        code="JOB-B2D1-1",
        status="ready",
    ):
        return PartnerJob.objects.create(
            code=code,
            order=self.order,
            service_execution=service_execution,
            partner=self.partner,
            status=status,
        )

    def test_legacy_partner_job_without_execution_still_handover(self):
        partner_job = self.create_partner_job(
            service_execution=None,
        )

        handover_partner_job(
            partner_job=partner_job,
            notes="Remise legacy",
        )

        partner_job.refresh_from_db()

        self.assertEqual(
            partner_job.status,
            "handed_over",
        )
        self.assertIsNotNone(
            partner_job.handed_over_at,
        )

    def test_last_required_partner_job_completes_execution(self):
        service = self.create_service()
        execution = self.create_execution(service)

        partner_job = self.create_partner_job(
            service_execution=execution,
        )

        handover_partner_job(
            partner_job=partner_job,
            notes="Remise finale",
        )

        partner_job.refresh_from_db()
        execution.refresh_from_db()

        self.assertEqual(
            partner_job.status,
            "handed_over",
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertIsNotNone(
            execution.completed_at,
        )

    def test_execution_waits_when_another_partner_job_is_not_handed_over(self):
        service = self.create_service()
        execution = self.create_execution(service)

        first_job = self.create_partner_job(
            service_execution=execution,
            code="JOB-B2D1-2",
        )

        self.create_partner_job(
            service_execution=execution,
            code="JOB-B2D1-3",
            status="ready",
        )

        handover_partner_job(
            partner_job=first_job,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(
            execution.completed_at,
        )

    def test_execution_waits_for_other_required_capabilities(self):
        service = self.create_service(
            requires_logistics=True,
        )
        execution = self.create_execution(service)

        Mission.objects.create(
            code="MSN-B2D1-1",
            order=self.order,
            service_execution=execution,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        partner_job = self.create_partner_job(
            service_execution=execution,
            code="JOB-B2D1-4",
        )

        handover_partner_job(
            partner_job=partner_job,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(
            execution.completed_at,
        )

    def test_terminal_execution_is_not_reopened(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_CANCELED,
        )

        partner_job = self.create_partner_job(
            service_execution=execution,
            code="JOB-B2D1-5",
        )

        handover_partner_job(
            partner_job=partner_job,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertIsNone(
            execution.completed_at,
        )
