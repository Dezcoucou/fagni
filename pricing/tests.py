from django.core.exceptions import ValidationError
from django.test import TestCase

from orders.models import Customer, Order
from pricing.models import PriceQuote
from pricing.services import create_estimated_quote
from services.models import Service, ServiceCategory, ServiceExecution


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
