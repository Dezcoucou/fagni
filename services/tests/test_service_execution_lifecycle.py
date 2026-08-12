from django.test import TestCase

from orders.models import Customer, Order
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import (
    await_service_execution_validation,
    cancel_service_execution,
    complete_service_execution,
    fail_service_execution,
    schedule_service_execution,
    start_service_execution,
)


class ServiceExecutionLifecycleTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="test-lifecycle-category",
            name="Test Lifecycle Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="test-lifecycle-service",
            category=self.category,
            name="Test Lifecycle Service",
            description="",
            is_active=True,
            primary_engine=Service.ENGINE_PICKUP_RETURN,
            requires_partner=False,
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

        self.customer = Customer.objects.create(
            name="Client Lifecycle",
            phone="0700006001",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

    def create_execution(self, status=ServiceExecution.STATUS_PENDING):
        return ServiceExecution.objects.create(
            order=self.order,
            service=self.service,
            execution_engine=self.service.primary_engine,
            status=status,
        )

    def test_pending_can_be_scheduled(self):
        execution = self.create_execution()

        schedule_service_execution(
            service_execution=execution,
            note="Planification test",
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_SCHEDULED,
        )
        self.assertIn("Planification test", execution.notes)

    def test_scheduled_can_be_started(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_SCHEDULED,
        )

        start_service_execution(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNotNone(execution.started_at)

    def test_in_progress_can_await_validation(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_IN_PROGRESS,
        )

        await_service_execution_validation(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_AWAITING_VALIDATION,
        )

    def test_awaiting_validation_can_return_to_in_progress(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_AWAITING_VALIDATION,
        )

        start_service_execution(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )

    def test_in_progress_can_be_completed(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_IN_PROGRESS,
        )

        complete_service_execution(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertIsNotNone(execution.completed_at)

    def test_awaiting_validation_can_be_completed(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_AWAITING_VALIDATION,
        )

        complete_service_execution(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )

    def test_pending_can_be_canceled(self):
        execution = self.create_execution()

        cancel_service_execution(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertIsNotNone(execution.canceled_at)

    def test_in_progress_can_fail(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_IN_PROGRESS,
        )

        fail_service_execution(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_FAILED,
        )

    def test_pending_cannot_jump_directly_to_completed(self):
        execution = self.create_execution()

        with self.assertRaises(ValueError):
            complete_service_execution(
                service_execution=execution,
            )

    def test_scheduled_cannot_jump_directly_to_completed(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_SCHEDULED,
        )

        with self.assertRaises(ValueError):
            complete_service_execution(
                service_execution=execution,
            )

    def test_completed_is_terminal(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_COMPLETED,
        )

        with self.assertRaises(ValueError):
            start_service_execution(
                service_execution=execution,
            )

        with self.assertRaises(ValueError):
            cancel_service_execution(
                service_execution=execution,
            )

        with self.assertRaises(ValueError):
            fail_service_execution(
                service_execution=execution,
            )

    def test_canceled_is_terminal(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_CANCELED,
        )

        with self.assertRaises(ValueError):
            start_service_execution(
                service_execution=execution,
            )

    def test_failed_is_terminal(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_FAILED,
        )

        with self.assertRaises(ValueError):
            start_service_execution(
                service_execution=execution,
            )

    def test_started_at_is_not_rewritten_after_validation_return(self):
        execution = self.create_execution(
            status=ServiceExecution.STATUS_SCHEDULED,
        )

        start_service_execution(
            service_execution=execution,
        )

        execution.refresh_from_db()
        first_started_at = execution.started_at

        await_service_execution_validation(
            service_execution=execution,
        )

        start_service_execution(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.started_at,
            first_started_at,
        )
