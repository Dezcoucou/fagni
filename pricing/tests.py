from django.core.exceptions import ValidationError
from django.test import TestCase

from orders.models import Customer, Order
from pricing.models import PriceQuote
from pricing.services import create_estimated_quote
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import (
    create_service_execution,
    schedule_service_execution,
    start_service_execution,
)


class PriceQuoteServiceExecutionContractTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="quote-contract-category",
            name="Quote Contract Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="quote-contract-service",
            category=self.category,
            name="Quote Contract Service",
            description="",
            is_active=True,
            primary_engine=Service.ENGINE_PICKUP_RETURN,
            requires_partner=False,
            requires_logistics=False,
            requires_weighing=False,
            requires_appointment=False,
            requires_quote=True,
            requires_asset=False,
            requires_otp=False,
            requires_signature=False,
            pricing_mode="quote_required",
            default_sla_hours=24,
        )

        self.customer_a = Customer.objects.create(
            name="Client Quote A",
            phone="0700009601",
        )
        self.customer_b = Customer.objects.create(
            name="Client Quote B",
            phone="0700009602",
        )

        self.order_a = Order.objects.create(
            customer=self.customer_a,
        )
        self.order_b = Order.objects.create(
            customer=self.customer_b,
        )

        self.execution_a = create_service_execution(
            order=self.order_a,
            service=self.service,
        )

        self.execution_b = create_service_execution(
            order=self.order_b,
            service=self.service,
        )

    def test_legacy_quote_without_service_execution_is_allowed(self):
        quote = create_estimated_quote(
            order=self.order_a,
        )

        self.assertIsNone(quote.service_execution)

    def test_quote_with_execution_from_same_order_is_allowed(self):
        quote = create_estimated_quote(
            order=self.order_a,
            service_execution=self.execution_a,
        )

        self.assertEqual(
            quote.service_execution_id,
            self.execution_a.id,
        )
        self.assertEqual(
            quote.order_id,
            self.execution_a.order_id,
        )

    def test_quote_model_rejects_execution_from_different_order(self):
        quote = PriceQuote(
            order=self.order_b,
            service_execution=self.execution_a,
            quote_type="estimated",
        )

        with self.assertRaises(ValidationError):
            quote.save()

    def test_quote_full_clean_rejects_execution_from_different_order(self):
        quote = PriceQuote(
            order=self.order_b,
            service_execution=self.execution_a,
            quote_type="estimated",
        )

        with self.assertRaises(ValidationError):
            quote.full_clean()

    def test_service_rejects_execution_from_different_order(self):
        with self.assertRaises(ValueError):
            create_estimated_quote(
                order=self.order_b,
                service_execution=self.execution_a,
            )


class PriceQuoteServiceExecutionCompletionTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="quote-completion-category",
            name="Quote Completion Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="quote-completion-service",
            category=self.category,
            name="Quote Completion Service",
            description="",
            is_active=True,
            primary_engine=Service.ENGINE_PICKUP_RETURN,
            requires_partner=False,
            requires_logistics=False,
            requires_weighing=False,
            requires_appointment=False,
            requires_quote=True,
            requires_asset=False,
            requires_otp=False,
            requires_signature=False,
            pricing_mode="quote_required",
            default_sla_hours=24,
        )

        self.customer = Customer.objects.create(
            name="Client Quote Completion",
            phone="0700009701",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

        self.execution = create_service_execution(
            order=self.order,
            service=self.service,
        )
        schedule_service_execution(
            service_execution=self.execution,
        )
        start_service_execution(
            service_execution=self.execution,
        )

    def test_estimated_quote_does_not_satisfy_quote_requirement(self):
        from services.services import evaluate_service_execution_completion

        create_estimated_quote(
            order=self.order,
            service_execution=self.execution,
        )

        result = evaluate_service_execution_completion(
            service_execution=self.execution,
        )

        self.assertFalse(result["ready"])
        self.assertIn("quote:no_final_quote", result["missing"])
        self.assertFalse(result["checks"]["quote"]["satisfied"])

    def test_final_flag_without_final_type_does_not_satisfy_requirement(self):
        from services.services import evaluate_service_execution_completion

        PriceQuote.objects.create(
            order=self.order,
            service_execution=self.execution,
            quote_type="estimated",
            is_final=True,
        )

        result = evaluate_service_execution_completion(
            service_execution=self.execution,
        )

        self.assertFalse(result["ready"])
        self.assertIn("quote:no_final_quote", result["missing"])

    def test_final_type_without_final_flag_does_not_satisfy_requirement(self):
        from services.services import evaluate_service_execution_completion

        PriceQuote.objects.create(
            order=self.order,
            service_execution=self.execution,
            quote_type="final",
            is_final=False,
        )

        result = evaluate_service_execution_completion(
            service_execution=self.execution,
        )

        self.assertFalse(result["ready"])
        self.assertIn("quote:no_final_quote", result["missing"])

    def test_final_quote_satisfies_quote_requirement(self):
        from services.services import evaluate_service_execution_completion

        PriceQuote.objects.create(
            order=self.order,
            service_execution=self.execution,
            quote_type="final",
            is_final=True,
        )

        result = evaluate_service_execution_completion(
            service_execution=self.execution,
        )

        self.assertTrue(result["ready"])
        self.assertNotIn("quote:no_final_quote", result["missing"])
        self.assertTrue(result["checks"]["quote"]["satisfied"])

    def test_finalize_quote_completes_ready_execution(self):
        from pricing.services import finalize_quote

        quote = create_estimated_quote(
            order=self.order,
            service_execution=self.execution,
        )

        finalize_quote(
            quote=quote,
            notes="Validation finale B2D5B",
        )

        quote.refresh_from_db()
        self.execution.refresh_from_db()

        self.assertEqual(quote.quote_type, "final")
        self.assertTrue(quote.is_final)

        self.assertEqual(
            self.execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertIsNotNone(self.execution.completed_at)

    def test_catalogue_change_after_creation_does_not_add_signature_requirement(
        self,
    ):
        from pricing.services import finalize_quote

        self.service.requires_signature = True
        self.service.save(update_fields=["requires_signature"])

        quote = create_estimated_quote(
            order=self.order,
            service_execution=self.execution,
        )

        finalize_quote(
            quote=quote,
        )

        self.execution.refresh_from_db()

        self.assertFalse(
            self.execution.service_snapshot_json[
                "requirements"
            ]["requires_signature"]
        )
        self.assertEqual(
            self.execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertIsNotNone(self.execution.completed_at)

    def test_finalize_quote_waits_for_signature_required_in_snapshot(
        self,
    ):
        from pricing.services import finalize_quote

        self.service.requires_signature = True
        self.service.save(update_fields=["requires_signature"])

        execution = create_service_execution(
            order=self.order,
            service=self.service,
        )
        schedule_service_execution(
            service_execution=execution,
        )
        start_service_execution(
            service_execution=execution,
        )

        quote = create_estimated_quote(
            order=self.order,
            service_execution=execution,
        )

        finalize_quote(
            quote=quote,
        )

        execution.refresh_from_db()

        self.assertTrue(
            execution.service_snapshot_json[
                "requirements"
            ]["requires_signature"]
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(execution.completed_at)

    def test_finalize_legacy_quote_without_execution_still_works(self):
        from pricing.services import finalize_quote

        quote = create_estimated_quote(
            order=self.order,
        )

        finalize_quote(
            quote=quote,
        )

        quote.refresh_from_db()

        self.assertEqual(quote.quote_type, "final")
        self.assertTrue(quote.is_final)
        self.assertIsNone(quote.service_execution_id)

    def test_finalize_quote_does_not_reopen_terminal_execution(self):
        from pricing.services import finalize_quote

        self.execution.status = ServiceExecution.STATUS_CANCELED
        self.execution.save(update_fields=["status"])

        quote = create_estimated_quote(
            order=self.order,
            service_execution=self.execution,
        )

        finalize_quote(
            quote=quote,
        )

        self.execution.refresh_from_db()

        self.assertEqual(
            self.execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertIsNone(self.execution.completed_at)
