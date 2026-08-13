from decimal import Decimal

from django.test import TestCase

from logistics.models import Mission
from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob
from production.services import record_weighing
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import (
    cancel_service_execution,
    create_service_execution,
    schedule_service_execution,
    start_service_execution,
)


class WeighingServiceExecutionAutoCompletionTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="weighing-auto-completion-category",
            name="Weighing Auto Completion Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Weighing Auto Completion",
            phone="0700009301",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

        self.partner = LaundryPartner.objects.create(
            name="Partner Weighing Auto Completion",
        )

    def create_service(self, **overrides):
        values = {
            "code": "weighing-auto-completion-service",
            "category": self.category,
            "name": "Weighing Auto Completion Service",
            "description": "",
            "is_active": True,
            "primary_engine": Service.ENGINE_PICKUP_RETURN,
            "requires_partner": False,
            "requires_logistics": False,
            "requires_weighing": True,
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

    def test_final_validation_completes_ready_execution(self):
        service = self.create_service()
        execution = self.create_execution(service)

        record = record_weighing(
            order=self.order,
            service_execution=execution,
            net_weight=Decimal("3.50"),
            weighing_stage="final_validation",
        )

        execution.refresh_from_db()

        self.assertEqual(
            record.service_execution_id,
            execution.id,
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertIsNotNone(execution.completed_at)

    def test_partner_reception_does_not_complete_execution(self):
        service = self.create_service()
        execution = self.create_execution(service)

        record_weighing(
            order=self.order,
            service_execution=execution,
            net_weight=Decimal("3.50"),
            weighing_stage="partner_reception",
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(execution.completed_at)

    def test_execution_is_inherited_from_partner_job(self):
        service = self.create_service()
        execution = self.create_execution(service)

        partner_job = PartnerJob.objects.create(
            code="JOB-B2D2-1",
            order=self.order,
            service_execution=execution,
            partner=self.partner,
            status="ready",
        )

        record = record_weighing(
            order=self.order,
            partner_job=partner_job,
            service_execution=None,
            net_weight=Decimal("3.50"),
            weighing_stage="final_validation",
        )

        execution.refresh_from_db()

        self.assertEqual(
            record.service_execution_id,
            execution.id,
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )

    def test_execution_is_inherited_from_mission(self):
        service = self.create_service()
        execution = self.create_execution(service)

        mission = Mission.objects.create(
            code="MSN-B2D2-1",
            order=self.order,
            service_execution=execution,
            mission_type="pickup_from_customer",
            status="completed",
        )

        record = record_weighing(
            order=self.order,
            mission=mission,
            service_execution=None,
            net_weight=Decimal("3.50"),
            weighing_stage="final_validation",
        )

        execution.refresh_from_db()

        self.assertEqual(
            record.service_execution_id,
            execution.id,
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )

    def test_legacy_weighing_without_execution_stays_legacy(self):
        record = record_weighing(
            order=self.order,
            net_weight=Decimal("3.50"),
            weighing_stage="final_validation",
        )

        self.assertIsNone(record.service_execution_id)

    def test_execution_waits_for_other_required_capabilities(self):
        service = self.create_service(
            requires_partner=True,
        )
        execution = self.create_execution(service)

        PartnerJob.objects.create(
            code="JOB-B2D2-2",
            order=self.order,
            service_execution=execution,
            partner=self.partner,
            status="ready",
        )

        record_weighing(
            order=self.order,
            service_execution=execution,
            net_weight=Decimal("3.50"),
            weighing_stage="final_validation",
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(execution.completed_at)

    def test_terminal_execution_is_not_reopened(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_CANCELED,
        )

        record_weighing(
            order=self.order,
            service_execution=execution,
            net_weight=Decimal("3.50"),
            weighing_stage="final_validation",
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertIsNone(execution.completed_at)

    def test_conflicting_mission_and_partner_job_executions_are_rejected(self):
        service = self.create_service()

        execution_a = self.create_execution(service)
        execution_b = self.create_execution(service)

        mission = Mission.objects.create(
            code="MSN-B2D2-CONFLICT",
            order=self.order,
            service_execution=execution_a,
            mission_type="pickup_from_customer",
            status="completed",
        )

        partner_job = PartnerJob.objects.create(
            code="JOB-B2D2-CONFLICT",
            order=self.order,
            service_execution=execution_b,
            partner=self.partner,
            status="ready",
        )

        with self.assertRaises(ValueError):
            record_weighing(
                order=self.order,
                mission=mission,
                partner_job=partner_job,
                net_weight=Decimal("3.50"),
                weighing_stage="final_validation",
            )
