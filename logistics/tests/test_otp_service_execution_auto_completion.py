from django.test import TestCase
from django.urls import reverse

from logistics.models import Mission
from logistics.models_otp import MissionOTP
from orders.models import Customer, Order
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import (
    cancel_service_execution,
    create_service_execution,
    schedule_service_execution,
    start_service_execution,
)


class OTPServiceExecutionAutoCompletionTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="otp-auto-completion-category",
            name="OTP Auto Completion Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client OTP Auto Completion",
            phone="0700009401",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

    def create_service(self, **overrides):
        values = {
            "code": "otp-auto-completion-service",
            "category": self.category,
            "name": "OTP Auto Completion Service",
            "description": "",
            "is_active": True,
            "primary_engine": Service.ENGINE_PICKUP_RETURN,
            "requires_partner": False,
            "requires_logistics": False,
            "requires_weighing": False,
            "requires_appointment": False,
            "requires_quote": False,
            "requires_asset": False,
            "requires_otp": True,
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

    def create_mission(
        self,
        *,
        service_execution=None,
        code="MSN-B2D3-1",
    ):
        return Mission.objects.create(
            code=code,
            order=self.order,
            service_execution=service_execution,
            mission_type="pickup_from_customer",
            status="completed",
        )

    def create_otp(
        self,
        *,
        mission,
        code="123456",
    ):
        return MissionOTP.objects.create(
            mission=mission,
            order=self.order,
            phone_number="0700009401",
            channel="whatsapp",
            provider="test",
            otp_code=code,
            status="sent",
        )

    def check_otp(self, mission, code="123456"):
        return self.client.post(
            reverse(
                "logistics:mission_check_otp_whatsapp",
                kwargs={"mission_id": mission.id},
            ),
            {
                "verification_code": code,
            },
        )

    def test_approved_otp_completes_ready_execution(self):
        service = self.create_service()
        execution = self.create_execution(service)

        mission = self.create_mission(
            service_execution=execution,
        )
        otp = self.create_otp(
            mission=mission,
        )

        response = self.check_otp(mission)

        otp.refresh_from_db()
        execution.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(otp.status, "approved")
        self.assertIsNotNone(otp.verified_at)

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertIsNotNone(execution.completed_at)

    def test_invalid_otp_does_not_complete_execution(self):
        service = self.create_service()
        execution = self.create_execution(service)

        mission = self.create_mission(
            service_execution=execution,
            code="MSN-B2D3-2",
        )
        otp = self.create_otp(
            mission=mission,
        )

        self.check_otp(
            mission,
            code="999999",
        )

        otp.refresh_from_db()
        execution.refresh_from_db()

        self.assertEqual(otp.status, "failed")
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(execution.completed_at)

    def test_approved_otp_waits_for_other_required_capabilities(self):
        service = self.create_service(
            requires_partner=True,
        )
        execution = self.create_execution(service)

        mission = self.create_mission(
            service_execution=execution,
            code="MSN-B2D3-3",
        )
        otp = self.create_otp(
            mission=mission,
        )

        self.check_otp(mission)

        otp.refresh_from_db()
        execution.refresh_from_db()

        self.assertEqual(otp.status, "approved")

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(execution.completed_at)

    def test_legacy_otp_without_execution_still_approves(self):
        mission = self.create_mission(
            service_execution=None,
            code="MSN-B2D3-4",
        )
        otp = self.create_otp(
            mission=mission,
        )

        self.check_otp(mission)

        otp.refresh_from_db()

        self.assertEqual(otp.status, "approved")
        self.assertIsNotNone(otp.verified_at)

    def test_terminal_execution_is_not_reopened(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_CANCELED,
        )

        mission = self.create_mission(
            service_execution=execution,
            code="MSN-B2D3-5",
        )
        otp = self.create_otp(
            mission=mission,
        )

        self.check_otp(mission)

        otp.refresh_from_db()
        execution.refresh_from_db()

        self.assertEqual(otp.status, "approved")
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertIsNone(execution.completed_at)
