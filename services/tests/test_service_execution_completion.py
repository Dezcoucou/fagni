from decimal import Decimal

from django.test import TestCase

from logistics.models import Mission
from logistics.models_otp import MissionOTP
from logistics.models_signature import MissionSignature
from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob, WeighingRecord
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import evaluate_service_execution_completion


class ServiceExecutionCompletionTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="test-completion-category",
            name="Test Completion Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Completion",
            phone="0700008001",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

        self.partner = LaundryPartner.objects.create(
            name="Blanchisserie Completion",
        )

    def create_service(self, **overrides):
        values = {
            "code": "test-completion-service",
            "category": self.category,
            "name": "Test Completion Service",
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

    def create_execution(self, service):
        return ServiceExecution.objects.create(
            order=self.order,
            service=service,
            execution_engine=service.primary_engine,
            status=ServiceExecution.STATUS_IN_PROGRESS,
        )

    def create_mission(self, execution, status="completed"):
        return Mission.objects.create(
            code=f"MSN-COMP-{Mission.objects.count() + 1}",
            order=self.order,
            service_execution=execution,
            mission_type="pickup_from_customer",
            status=status,
        )

    def test_service_without_requirements_is_ready(self):
        service = self.create_service()
        execution = self.create_execution(service)

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertTrue(result["ready"])
        self.assertEqual(result["missing"], [])

    def test_logistics_requires_at_least_one_mission(self):
        service = self.create_service(
            requires_logistics=True,
        )
        execution = self.create_execution(service)

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertFalse(result["ready"])
        self.assertIn(
            "logistics:no_mission",
            result["missing"],
        )

    def test_all_missions_must_be_completed(self):
        service = self.create_service(
            requires_logistics=True,
        )
        execution = self.create_execution(service)

        self.create_mission(execution, status="completed")
        self.create_mission(execution, status="en_route")

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertFalse(result["ready"])
        self.assertIn(
            "logistics:missions_not_completed",
            result["missing"],
        )

    def test_completed_missions_satisfy_logistics(self):
        service = self.create_service(
            requires_logistics=True,
        )
        execution = self.create_execution(service)

        self.create_mission(execution, status="completed")

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertTrue(result["ready"])

    def test_partner_job_ready_is_not_terminal(self):
        service = self.create_service(
            requires_partner=True,
        )
        execution = self.create_execution(service)

        PartnerJob.objects.create(
            code="JOB-COMP-READY",
            order=self.order,
            service_execution=execution,
            partner=self.partner,
            status="ready",
        )

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertFalse(result["ready"])
        self.assertIn(
            "partner:jobs_not_handed_over",
            result["missing"],
        )

    def test_partner_job_handed_over_satisfies_partner(self):
        service = self.create_service(
            requires_partner=True,
        )
        execution = self.create_execution(service)

        PartnerJob.objects.create(
            code="JOB-COMP-HANDOVER",
            order=self.order,
            service_execution=execution,
            partner=self.partner,
            status="handed_over",
        )

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertTrue(result["ready"])

    def test_partner_reception_weighing_is_not_final_validation(self):
        service = self.create_service(
            requires_weighing=True,
        )
        execution = self.create_execution(service)

        WeighingRecord.objects.create(
            order=self.order,
            service_execution=execution,
            performed_by_role="partner",
            weighing_stage="partner_reception",
            net_weight=Decimal("3.50"),
            unit="kg",
        )

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertFalse(result["ready"])
        self.assertIn(
            "weighing:no_final_validation",
            result["missing"],
        )

    def test_final_validation_weighing_satisfies_weighing(self):
        service = self.create_service(
            requires_weighing=True,
        )
        execution = self.create_execution(service)

        WeighingRecord.objects.create(
            order=self.order,
            service_execution=execution,
            performed_by_role="partner",
            weighing_stage="final_validation",
            net_weight=Decimal("3.50"),
            unit="kg",
        )

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertTrue(result["ready"])

    def test_approved_otp_satisfies_otp_requirement(self):
        service = self.create_service(
            requires_otp=True,
        )
        execution = self.create_execution(service)
        mission = self.create_mission(execution)

        MissionOTP.objects.create(
            mission=mission,
            order=self.order,
            phone_number="0700008001",
            channel="whatsapp",
            provider="test",
            otp_code="123456",
            status="approved",
        )

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertTrue(result["ready"])

    def test_validated_signature_satisfies_signature_requirement(self):
        service = self.create_service(
            requires_signature=True,
        )
        execution = self.create_execution(service)
        mission = self.create_mission(execution)

        MissionSignature.objects.create(
            mission=mission,
            order=self.order,
            signer_name="Client Completion",
            status="validated",
        )

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertTrue(result["ready"])

    def test_unresolved_capabilities_block_completion(self):
        service = self.create_service(
            requires_appointment=True,
            requires_asset=True,
        )
        execution = self.create_execution(service)

        result = evaluate_service_execution_completion(
            service_execution=execution,
        )

        self.assertFalse(result["ready"])

        self.assertIn("unresolved:appointment", result["missing"])
        self.assertIn("unresolved:asset", result["missing"])
