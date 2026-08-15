from django.test import TestCase

from logistics.orchestrator import run_minimal_v2_flow
from orders.models import Customer, Order
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import (
    create_service_execution,
    schedule_service_execution,
    start_service_execution,
)


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
        execution = create_service_execution(
            order=self.order,
            service=self.service,
        )

        if status == ServiceExecution.STATUS_PENDING:
            return execution

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

    def test_legacy_flow_without_service_execution_still_works(self):
        result = run_minimal_v2_flow(
            order=self.order,
            service_execution=None,
        )

        self.assertIsNone(result["service_execution"])
        self.assertIsNone(result["mission"].service_execution)
        self.assertIsNone(result["partner_job"].service_execution)
        self.assertIsNone(result["weighing_record"].service_execution)
        self.assertIsNone(result["quote"].service_execution)

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
        self.assertEqual(
            result["quote"].service_execution_id,
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


class OrchestratorServiceExecutionResolutionTests(TestCase):
    """
    Contrat d'entrée de l'orchestrateur multiservices.

    Le moteur ne doit jamais choisir arbitrairement une exécution
    lorsqu'une Order en possède plusieurs.
    """

    def setUp(self):
        from logistics.orchestrator import (
            resolve_service_execution_for_orchestration,
        )

        self.resolve = resolve_service_execution_for_orchestration

        self.category = ServiceCategory.objects.create(
            code="orchestration-resolution-category",
            name="Orchestration Resolution Category",
            is_active=True,
        )

        self.service_a = Service.objects.create(
            code="orchestration-resolution-service-a",
            category=self.category,
            name="Orchestration Resolution Service A",
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

        self.service_b = Service.objects.create(
            code="orchestration-resolution-service-b",
            category=self.category,
            name="Orchestration Resolution Service B",
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

        self.customer = Customer.objects.create(
            name="Client Orchestration Resolution",
            phone="0700007002",
        )

        self.order_a = Order.objects.create(
            customer=self.customer,
            pickup_address="Cocody",
            delivery_address="Riviera",
        )

        self.order_b = Order.objects.create(
            customer=self.customer,
            pickup_address="Cocody",
            delivery_address="Riviera",
        )

    def test_no_execution_preserves_legacy_none(self):
        resolved = self.resolve(
            order=self.order_a,
            service_execution=None,
        )

        self.assertIsNone(resolved)

    def test_single_execution_is_resolved_automatically(self):
        execution = create_service_execution(
            order=self.order_a,
            service=self.service_a,
        )

        resolved = self.resolve(
            order=self.order_a,
            service_execution=None,
        )

        self.assertEqual(
            resolved.id,
            execution.id,
        )

    def test_explicit_execution_from_same_order_is_used(self):
        execution = create_service_execution(
            order=self.order_a,
            service=self.service_a,
        )

        resolved = self.resolve(
            order=self.order_a,
            service_execution=execution,
        )

        self.assertEqual(
            resolved.id,
            execution.id,
        )

    def test_explicit_execution_from_other_order_is_rejected(self):
        from logistics.orchestrator import (
            ServiceExecutionOrchestrationError,
        )

        execution = create_service_execution(
            order=self.order_b,
            service=self.service_a,
        )

        with self.assertRaises(
            ServiceExecutionOrchestrationError
        ):
            self.resolve(
                order=self.order_a,
                service_execution=execution,
            )

    def test_multiple_executions_without_explicit_choice_are_rejected(self):
        from logistics.orchestrator import (
            AmbiguousServiceExecutionError,
        )

        create_service_execution(
            order=self.order_a,
            service=self.service_a,
        )

        create_service_execution(
            order=self.order_a,
            service=self.service_b,
        )

        with self.assertRaises(
            AmbiguousServiceExecutionError
        ):
            self.resolve(
                order=self.order_a,
                service_execution=None,
            )

    def test_explicit_execution_resolves_multiservice_ambiguity(self):
        execution_a = create_service_execution(
            order=self.order_a,
            service=self.service_a,
        )

        create_service_execution(
            order=self.order_a,
            service=self.service_b,
        )

        resolved = self.resolve(
            order=self.order_a,
            service_execution=execution_a,
        )

        self.assertEqual(
            resolved.id,
            execution_a.id,
        )

    def test_unsaved_order_is_rejected(self):
        from logistics.orchestrator import (
            ServiceExecutionOrchestrationError,
        )

        unsaved_order = Order(
            customer=self.customer,
        )

        with self.assertRaises(
            ServiceExecutionOrchestrationError
        ):
            self.resolve(
                order=unsaved_order,
            )
