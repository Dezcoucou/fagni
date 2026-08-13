from django.test import TestCase

from logistics.models import Mission
from logistics.services import complete_mission
from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob
from services.models import Service, ServiceCategory, ServiceExecution
from services.services import (
    cancel_service_execution,
    create_service_execution,
    schedule_service_execution,
    start_service_execution,
)


class MissionServiceExecutionAutoCompletionTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="mission-auto-completion-category",
            name="Mission Auto Completion Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Mission Auto Completion",
            phone="0700009101",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

        self.partner = LaundryPartner.objects.create(
            name="Partner Mission Auto Completion",
        )

    def create_service(self, **overrides):
        values = {
            "code": "mission-auto-completion-service",
            "category": self.category,
            "name": "Mission Auto Completion Service",
            "description": "",
            "is_active": True,
            "primary_engine": Service.ENGINE_PICKUP_RETURN,
            "requires_partner": False,
            "requires_logistics": True,
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
        code="MSN-B2C-1",
        status="en_route",
    ):
        return Mission.objects.create(
            code=code,
            order=self.order,
            service_execution=service_execution,
            mission_type="pickup_from_customer",
            status=status,
        )

    def test_legacy_mission_without_execution_still_completes(self):
        mission = self.create_mission(
            service_execution=None,
        )

        complete_mission(
            mission=mission,
            notes="Fin legacy",
        )

        mission.refresh_from_db()

        self.assertEqual(
            mission.status,
            "completed",
        )
        self.assertIsNotNone(
            mission.completed_at,
        )

    def test_last_required_mission_completes_execution(self):
        service = self.create_service()
        execution = self.create_execution(service)

        mission = self.create_mission(
            service_execution=execution,
        )

        complete_mission(
            mission=mission,
            notes="Fin mission déclenchante",
        )

        mission.refresh_from_db()
        execution.refresh_from_db()

        self.assertEqual(
            mission.status,
            "completed",
        )
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_COMPLETED,
        )
        self.assertIsNotNone(
            execution.completed_at,
        )

    def test_execution_waits_when_another_mission_is_not_completed(self):
        service = self.create_service()
        execution = self.create_execution(service)

        first_mission = self.create_mission(
            service_execution=execution,
            code="MSN-B2C-2",
        )

        self.create_mission(
            service_execution=execution,
            code="MSN-B2C-3",
            status="assigned",
        )

        complete_mission(
            mission=first_mission,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(
            execution.completed_at,
        )

    def test_execution_waits_for_other_required_capabilities(self):
        service = self.create_service(
            requires_partner=True,
        )
        execution = self.create_execution(service)

        mission = self.create_mission(
            service_execution=execution,
            code="MSN-B2C-4",
        )

        PartnerJob.objects.create(
            code="JOB-B2C-1",
            order=self.order,
            service_execution=execution,
            partner=self.partner,
            status="ready",
        )

        complete_mission(
            mission=mission,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNone(
            execution.completed_at,
        )

    def test_terminal_execution_is_not_reopened(self):
        service = self.create_service()
        execution = self.create_execution(
            service,
            status=ServiceExecution.STATUS_CANCELED,
        )

        mission = self.create_mission(
            service_execution=execution,
            code="MSN-B2C-5",
        )

        complete_mission(
            mission=mission,
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertIsNone(
            execution.completed_at,
        )
