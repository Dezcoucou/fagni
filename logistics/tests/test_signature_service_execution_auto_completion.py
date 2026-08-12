import base64

from django.test import TestCase
from django.urls import reverse

from logistics.models import Mission
from logistics.models_signature import MissionSignature
from orders.models import Customer, Order
from services.models import Service, ServiceCategory, ServiceExecution


class SignatureServiceExecutionAutoCompletionTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="signature-auto-completion-category",
            name="Signature Auto Completion Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Signature Auto Completion",
            phone="0700009501",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

    def create_service(self, **overrides):
        values = {
            "code": "signature-auto-completion-service",
            "category": self.category,
            "name": "Signature Auto Completion Service",
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
            "requires_signature": True,
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
        return ServiceExecution.objects.create(
            order=self.order,
            service=service,
            execution_engine=service.primary_engine,
            status=status,
        )

    def create_mission(
        self,
        *,
        service_execution=None,
        code="MSN-B2D4-1",
    ):
        return Mission.objects.create(
            code=code,
            order=self.order,
            service_execution=service_execution,
            mission_type="pickup_from_customer",
            status="completed",
        )

    def submit_signature(self, mission):
        # PNG minimal suffisant pour tester le endpoint :
        # le endpoint décode uniquement le base64 avant stockage.
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"
            "CAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "YAAAAAYAAjCB0C8AAAAASUVORK5CYII="
        )

        signature_data = (
            "data:image/png;base64,"
            + base64.b64encode(png_bytes).decode("ascii")
        )

        return self.client.post(
            reverse(
                "logistics:create_signature_v2",
                kwargs={"mission_id": mission.id},
            ),
            {
                "signature_data": signature_data,
                "signer_name": "Client Signature",
                "signature_notes": "Validation test B2D4",
            },
        )

    def test_validated_signature_completes_ready_execution(self):
        service = self.create_service()
        execution = self.create_execution(service)

        mission = self.create_mission(
            service_execution=execution,
        )

        response = self.submit_signature(mission)

        execution.refresh_from_db()

        signature = MissionSignature.objects.get(
            mission=mission,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(signature.status, "validated")

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertIsNotNone(execution.completed_at)

    def test_validated_signature_waits_for_other_required_capabilities(self):
        service = self.create_service(
            requires_partner=True,
        )
        execution = self.create_execution(service)

        mission = self.create_mission(
            service_execution=execution,
            code="MSN-B2D4-2",
        )

        self.submit_signature(mission)

        execution.refresh_from_db()

        signature = MissionSignature.objects.get(
            mission=mission,
        )

        self.assertEqual(signature.status, "validated")
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(execution.completed_at)

    def test_legacy_signature_without_execution_still_validates(self):
        mission = self.create_mission(
            service_execution=None,
            code="MSN-B2D4-3",
        )

        response = self.submit_signature(mission)

        signature = MissionSignature.objects.get(
            mission=mission,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(signature.status, "validated")
        self.assertIsNone(mission.service_execution_id)

    def test_terminal_execution_is_not_reopened(self):
        service = self.create_service()

        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_CANCELED,
        )

        mission = self.create_mission(
            service_execution=execution,
            code="MSN-B2D4-4",
        )

        self.submit_signature(mission)

        execution.refresh_from_db()

        signature = MissionSignature.objects.get(
            mission=mission,
        )

        self.assertEqual(signature.status, "validated")
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertIsNone(execution.completed_at)

    def test_invalid_signature_payload_does_not_complete_execution(self):
        service = self.create_service()
        execution = self.create_execution(service)

        mission = self.create_mission(
            service_execution=execution,
            code="MSN-B2D4-5",
        )

        response = self.client.post(
            reverse(
                "logistics:create_signature_v2",
                kwargs={"mission_id": mission.id},
            ),
            {
                "signature_data": "invalid-data",
                "signer_name": "Client Signature",
            },
        )

        execution.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            MissionSignature.objects.filter(
                mission=mission,
            ).exists()
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(execution.completed_at)
