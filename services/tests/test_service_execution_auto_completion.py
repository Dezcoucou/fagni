from django.test import TestCase

from orders.models import Customer, Order
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import (
    await_service_execution_validation,
    cancel_service_execution,
    complete_service_execution,
    complete_service_execution_if_ready,
    create_service_execution,
    fail_service_execution,
    schedule_service_execution,
    start_service_execution,
)


class ServiceExecutionAutoCompletionTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="test-auto-completion-category",
            name="Test Auto Completion Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Auto Completion",
            phone="0700008101",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

    def create_service(self, **overrides):
        values = {
            "code": "test-auto-completion-service",
            "category": self.category,
            "name": "Test Auto Completion Service",
            "description": "",
            "is_active": True,
            "primary_engine": Service.ENGINE_PICKUP_RETURN,
            "requires_partner": False,
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

        if status == ServiceExecution.STATUS_FAILED:
            return fail_service_execution(
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

        if status == ServiceExecution.STATUS_AWAITING_VALIDATION:
            return await_service_execution_validation(
                service_execution=execution,
            )

        if status == ServiceExecution.STATUS_COMPLETED:
            return complete_service_execution(
                service_execution=execution,
            )

        raise ValueError(
            "Statut de test ServiceExecution non supporté : "
            f"{status}."
        )

    def test_ready_in_progress_execution_is_completed(self):
        service = self.create_service()
        execution = self.create_execution(service)

        result = complete_service_execution_if_ready(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertTrue(result["completed"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["reason"], "completed")

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertIsNotNone(execution.completed_at)

    def test_ready_awaiting_validation_execution_is_completed(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_AWAITING_VALIDATION,
        )

        result = complete_service_execution_if_ready(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertTrue(result["completed"])
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )

    def test_not_ready_execution_is_not_completed(self):
        service = self.create_service(
            requires_logistics=True,
        )
        execution = self.create_execution(service)

        result = complete_service_execution_if_ready(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertFalse(result["completed"])
        self.assertFalse(result["ready"])
        self.assertEqual(
            result["reason"],
            "requirements_not_satisfied",
        )
        self.assertIn(
            "logistics:no_mission",
            result["missing"],
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(execution.completed_at)

    def test_pending_execution_is_never_completed_implicitly(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_PENDING,
        )

        result = complete_service_execution_if_ready(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertFalse(result["completed"])
        self.assertTrue(result["ready"])
        self.assertEqual(
            result["reason"],
            "status_not_completable",
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_PENDING,
        )

    def test_scheduled_execution_is_never_completed_implicitly(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_SCHEDULED,
        )

        result = complete_service_execution_if_ready(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertFalse(result["completed"])
        self.assertTrue(result["ready"])
        self.assertEqual(
            result["reason"],
            "status_not_completable",
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_SCHEDULED,
        )

    def test_completed_execution_is_idempotent(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_COMPLETED,
        )

        result = complete_service_execution_if_ready(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertTrue(result["completed"])
        self.assertEqual(
            result["reason"],
            "terminal_status",
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )

    def test_canceled_execution_is_not_reopened(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_CANCELED,
        )

        result = complete_service_execution_if_ready(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertFalse(result["completed"])
        self.assertEqual(
            result["reason"],
            "terminal_status",
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_CANCELED,
        )

    def test_failed_execution_is_not_reopened(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_FAILED,
        )

        result = complete_service_execution_if_ready(
            service_execution=execution,
        )

        execution.refresh_from_db()

        self.assertFalse(result["completed"])
        self.assertEqual(
            result["reason"],
            "terminal_status",
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_FAILED,
        )
