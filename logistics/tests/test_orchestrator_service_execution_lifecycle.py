from django.test import TestCase

from logistics.orchestrator import run_minimal_v2_flow
from orders.models import Customer, Order
from services.models import Service, ServiceCategory, ServiceExecution


class OrchestratorServiceExecutionLifecycleTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="test-orchestrator-lifecycle-category",
            name="Test Orchestrator Lifecycle Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="test-orchestrator-lifecycle-service",
            category=self.category,
            name="Test Orchestrator Lifecycle Service",
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

        self.customer = Customer.objects.create(
            name="Client Orchestrator Lifecycle",
            phone="0700007001",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            pickup_address="Cocody Angré 8e Tranche",
            delivery_address="Cocody Riviera Palmeraie",
        )

    def create_execution(self, status=ServiceExecution.STATUS_PENDING):
        return ServiceExecution.objects.create(
            order=self.order,
            service=self.service,
            execution_engine=self.service.primary_engine,
            status=status,
        )

    def test_legacy_flow_without_service_execution_still_works(self):
        result = run_minimal_v2_flow(
            order=self.order,
            service_execution=None,
        )

        self.assertIsNone(result["service_execution"])
        self.assertIsNone(result["mission"].service_execution)
        self.assertIsNone(result["partner_job"].service_execution)
        self.assertIsNone(result["weighing_record"].service_execution)

    def test_pending_execution_is_started_by_orchestrator(self):
        execution = self.create_execution()

        result = run_minimal_v2_flow(
            order=self.order,
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNotNone(execution.started_at)

        self.assertEqual(
            result["mission"].service_execution_id,
            execution.id,
        )
        self.assertEqual(
            result["partner_job"].service_execution_id,
            execution.id,
        )
        self.assertEqual(
            result["weighing_record"].service_execution_id,
            execution.id,
        )

    def test_scheduled_execution_is_started_by_orchestrator(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_SCHEDULED,
        )

        run_minimal_v2_flow(
            order=self.order,
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNotNone(execution.started_at)

    def test_in_progress_execution_is_not_restarted(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_SCHEDULED,
        )

        from services.services import start_service_execution

        start_service_execution(
            service_execution=execution,
            note="Premier démarrage",
        )

        execution.refresh_from_db()
        first_started_at = execution.started_at

        run_minimal_v2_flow(
            order=self.order,
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertEqual(
            execution.started_at,
            first_started_at,
        )

    def test_orchestrator_keeps_execution_in_progress_after_minimal_flow(self):
        execution = self.create_execution()

        run_minimal_v2_flow(
            order=self.order,
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(execution.completed_at)
