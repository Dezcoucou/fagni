from django.test import TestCase

from logistics.models import Mission
from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob
from services.cancellation import (
    CommercialOrderCancellationError,
    cancel_commercial_order,
)
from services.models import Service, ServiceExecution
from services.services import create_service_execution


class CommercialOrderCancellationTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client Cancel Commercial",
            phone="0700000099",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status="pending",
            notes="Note historique",
        )

        self.partner = LaundryPartner.objects.create(
            name="Pressing Cancel Commercial",
        )

        self.service = Service.objects.create(
            code="cancel-commercial-test",
            name="Service Cancel Commercial",
            primary_engine=Service.ENGINE_PICKUP_RETURN,
            requires_partner=True,
            requires_logistics=True,
            is_active=True,
        )

        self.execution = create_service_execution(
            order=self.order,
            service=self.service,
        )

        self.mission = Mission.objects.create(
            code="MSN-CANCEL-COMM-001",
            order=self.order,
            service_execution=self.execution,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        self.partner_job = PartnerJob.objects.create(
            code="JOB-CANCEL-COMM-001",
            order=self.order,
            service_execution=self.execution,
            partner=self.partner,
            status="awaiting_reception",
        )

    def test_cancel_commercial_order_cascades_v2(self):
        result = cancel_commercial_order(
            order=self.order,
            reason="Demande client",
            notes="Test cascade",
        )

        self.order.refresh_from_db()
        self.execution.refresh_from_db()
        self.mission.refresh_from_db()
        self.partner_job.refresh_from_db()

        self.assertTrue(result["canceled"])
        self.assertFalse(result["already_canceled"])

        self.assertEqual(self.order.status, "canceled")
        self.assertEqual(
            self.execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertEqual(self.mission.status, "canceled")
        self.assertEqual(self.partner_job.status, "canceled")

        self.assertIsNotNone(self.mission.canceled_at)
        self.assertIsNotNone(self.partner_job.canceled_at)

        self.assertEqual(
            result["service_executions_canceled"],
            1,
        )
        self.assertEqual(
            result["missions_canceled"],
            1,
        )
        self.assertEqual(
            result["partner_jobs_canceled"],
            1,
        )

        self.assertIn(
            "ANNULATION: Demande client",
            self.order.notes,
        )
        self.assertIn(
            "Note historique",
            self.order.notes,
        )

    def test_cancel_commercial_order_is_idempotent(self):
        cancel_commercial_order(
            order=self.order,
            reason="Demande client",
        )

        result = cancel_commercial_order(
            order=self.order,
            reason="Retry API",
        )

        self.assertTrue(result["canceled"])
        self.assertTrue(result["already_canceled"])
        self.assertEqual(
            result["service_executions_canceled"],
            0,
        )
        self.assertEqual(
            result["missions_canceled"],
            0,
        )
        self.assertEqual(
            result["partner_jobs_canceled"],
            0,
        )

    def test_cancel_requires_reason(self):
        with self.assertRaises(CommercialOrderCancellationError):
            cancel_commercial_order(
                order=self.order,
                reason="",
            )

    def test_done_order_cannot_be_canceled(self):
        Order.objects.filter(pk=self.order.pk).update(
            status="done",
        )
        self.order.refresh_from_db()

        with self.assertRaises(CommercialOrderCancellationError):
            cancel_commercial_order(
                order=self.order,
                reason="Tentative invalide",
            )

    def test_completed_execution_blocks_cancellation(self):
        ServiceExecution.objects.filter(
            pk=self.execution.pk,
        ).update(
            status=ServiceExecution.STATUS_COMPLETED,
        )

        with self.assertRaises(CommercialOrderCancellationError):
            cancel_commercial_order(
                order=self.order,
                reason="Tentative invalide",
            )

        self.order.refresh_from_db()
        self.mission.refresh_from_db()
        self.partner_job.refresh_from_db()

        self.assertNotEqual(
            self.order.status,
            "canceled",
        )
        self.assertNotEqual(
            self.mission.status,
            "canceled",
        )
        self.assertNotEqual(
            self.partner_job.status,
            "canceled",
        )

    def test_handed_over_partner_job_blocks_cancellation(self):
        PartnerJob.objects.filter(
            pk=self.partner_job.pk,
        ).update(
            status="handed_over",
        )

        with self.assertRaises(CommercialOrderCancellationError):
            cancel_commercial_order(
                order=self.order,
                reason="Tentative après remise",
            )

        self.order.refresh_from_db()
        self.execution.refresh_from_db()
        self.mission.refresh_from_db()
        self.partner_job.refresh_from_db()

        self.assertNotEqual(
            self.order.status,
            "canceled",
        )
        self.assertNotEqual(
            self.execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertNotEqual(
            self.mission.status,
            "canceled",
        )
        self.assertEqual(
            self.partner_job.status,
            "handed_over",
        )

    def test_completed_mission_is_preserved(self):
        Mission.objects.filter(
            pk=self.mission.pk,
        ).update(
            status="completed",
        )

        result = cancel_commercial_order(
            order=self.order,
            reason="Annulation partielle contrôlée",
        )

        self.mission.refresh_from_db()
        self.execution.refresh_from_db()
        self.partner_job.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(
            self.mission.status,
            "completed",
        )
        self.assertEqual(
            self.partner_job.status,
            "canceled",
        )
        self.assertEqual(
            self.execution.status,
            ServiceExecution.STATUS_CANCELED,
        )
        self.assertEqual(
            self.order.status,
            "canceled",
        )
        self.assertEqual(
            result["missions_canceled"],
            0,
        )

    def test_legacy_order_without_execution_can_be_canceled(self):
        legacy_order = Order.objects.create(
            customer=self.customer,
            status="pending",
            notes="Legacy",
        )

        result = cancel_commercial_order(
            order=legacy_order,
            reason="Annulation legacy",
        )

        legacy_order.refresh_from_db()

        self.assertTrue(result["canceled"])
        self.assertEqual(
            legacy_order.status,
            "canceled",
        )
        self.assertEqual(
            result["service_executions_canceled"],
            0,
        )
        self.assertIn(
            "ANNULATION: Annulation legacy",
            legacy_order.notes,
        )
