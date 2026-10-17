import jwt

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from orders.models import Customer, Order
from partners.models import LaundryPartner
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import create_service_execution
from production.services import create_partner_job


class PartnerOrdersApiTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="partner-orders-test-category",
            name="Catégorie test API pressing",
            is_active=True,
        )
        self.service = Service.objects.create(
            code="pressing_bag",
            category=self.category,
            name="Pressing sac test",
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
        self.partner = LaundryPartner.objects.create(
            name="Pressing API test",
            phone="0700009001",
            is_active=True,
        )
        self.other_partner = LaundryPartner.objects.create(
            name="Autre pressing API test",
            phone="0700009002",
            is_active=True,
        )
        self.url = reverse("api-partner-orders")

    def _make_order(self, partner, phone):
        customer = Customer.objects.create(
            name="Client API test",
            phone=phone,
            address="Riviera 3",
        )
        return Order.objects.create(
            customer=customer,
            laundry_partner=partner,
            status="in_progress",
            pickup_address="Riviera 3",
            delivery_address="Riviera 3",
        )

    def _get_orders(self, partner):
        token = jwt.encode(
            {"pid": partner.id, "name": partner.name},
            settings.SECRET_KEY,
            algorithm="HS256",
        )
        return self.client.get(
            self.url,
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_v2_order_returns_execution_and_partner_job_status(self):
        order = self._make_order(self.partner, "0700009101")
        execution = create_service_execution(
            order=order,
            service=self.service,
        )
        job = create_partner_job(
            order=order,
            partner=self.partner,
            service_execution=execution,
        )
        execution.status = ServiceExecution.STATUS_IN_PROGRESS
        execution.save(update_fields=["status"])
        job.status = "processing"
        job.save(update_fields=["status"])

        response = self._get_orders(self.partner)

        self.assertEqual(response.status_code, 200)
        result = next(item for item in response.json()["orders"] if item["id"] == order.id)
        self.assertEqual(result["service_execution_id"], execution.id)
        self.assertEqual(result["service_execution_status"], "in_progress")
        self.assertEqual(result["partner_job_status"], "processing")

    def test_pressing_only_receives_its_own_orders(self):
        own_order = self._make_order(self.partner, "0700009102")
        foreign_order = self._make_order(self.other_partner, "0700009103")

        response = self._get_orders(self.partner)

        self.assertEqual(response.status_code, 200)
        returned_ids = {item["id"] for item in response.json()["orders"]}
        self.assertIn(own_order.id, returned_ids)
        self.assertNotIn(foreign_order.id, returned_ids)

    def test_legacy_order_without_execution_remains_visible(self):
        order = self._make_order(self.partner, "0700009104")

        response = self._get_orders(self.partner)

        self.assertEqual(response.status_code, 200)
        result = next(item for item in response.json()["orders"] if item["id"] == order.id)
        self.assertIsNone(result["service_execution_id"])
        self.assertIsNone(result["service_execution_status"])
        self.assertIsNone(result["partner_job_status"])

    def test_multiple_executions_returns_first_operational_pressing_execution(self):
        order = self._make_order(self.partner, "0700009105")

        first_execution = create_service_execution(
            order=order,
            service=self.service,
        )
        first_execution.sequence_index = 1
        first_execution.status = ServiceExecution.STATUS_COMPLETED
        first_execution.save(update_fields=["sequence_index", "status"])

        second_execution = create_service_execution(
            order=order,
            service=self.service,
        )
        second_execution.sequence_index = 2
        second_execution.status = ServiceExecution.STATUS_IN_PROGRESS
        second_execution.save(update_fields=["sequence_index", "status"])

        job = create_partner_job(
            order=order,
            partner=self.partner,
            service_execution=second_execution,
        )
        job.status = "processing"
        job.save(update_fields=["status"])

        response = self._get_orders(self.partner)

        self.assertEqual(response.status_code, 200)
        result = next(
            item for item in response.json()["orders"]
            if item["id"] == order.id
        )
        self.assertEqual(result["service_execution_id"], second_execution.id)
        self.assertEqual(
            result["service_execution_status"],
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertEqual(result["partner_job_status"], "processing")
