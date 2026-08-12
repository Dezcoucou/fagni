from django.core.exceptions import ValidationError
from django.test import TestCase

from logistics.models import Mission
from logistics.models_proof import ProofOfDelivery
from orders.models import Customer, Order
from services.models import Service, ServiceCategory, ServiceExecution


class ProofServiceExecutionContractTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="test-proof-category",
            name="Test Proof Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="test-proof-service",
            category=self.category,
            name="Test Proof Service",
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

        self.customer_a = Customer.objects.create(
            name="Client Proof A",
            phone="0700005001",
        )

        self.customer_b = Customer.objects.create(
            name="Client Proof B",
            phone="0700005002",
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

        self.mission_a = Mission.objects.create(
            code="MSN-PROOF-A",
            order=self.order_a,
            service_execution=self.execution_a,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        self.mission_b = Mission.objects.create(
            code="MSN-PROOF-B",
            order=self.order_b,
            service_execution=self.execution_b,
            mission_type="pickup_from_customer",
            status="assigned",
        )

    def test_legacy_proof_without_service_execution_is_allowed(self):
        proof = ProofOfDelivery.objects.create(
            mission=self.mission_a,
            order=self.order_a,
        )

        self.assertIsNone(proof.service_execution)

    def test_proof_with_same_execution_is_allowed(self):
        proof = ProofOfDelivery.objects.create(
            mission=self.mission_a,
            order=self.order_a,
            service_execution=self.execution_a,
        )

        self.assertEqual(
            proof.service_execution_id,
            self.execution_a.id,
        )

    def test_proof_with_execution_from_different_order_is_rejected(self):
        proof = ProofOfDelivery(
            mission=self.mission_b,
            order=self.order_b,
            service_execution=self.execution_a,
        )

        with self.assertRaises(ValidationError):
            proof.save()

    def test_proof_with_mission_from_other_execution_is_rejected(self):
        proof = ProofOfDelivery(
            mission=self.mission_b,
            order=self.order_b,
            service_execution=self.execution_a,
        )

        with self.assertRaises(ValidationError):
            proof.full_clean()

    def test_proof_with_mission_from_other_order_is_rejected(self):
        proof = ProofOfDelivery(
            mission=self.mission_b,
            order=self.order_a,
            service_execution=self.execution_a,
        )

        with self.assertRaises(ValidationError):
            proof.save()
