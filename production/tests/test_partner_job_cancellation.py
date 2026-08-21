from django.test import TestCase

from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob
from production.services import (
    cancel_partner_job,
    handover_partner_job,
    record_weighing,
)


class PartnerJobCancellationTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client Test Cancel PartnerJob",
            phone="0700000001",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status="pending",
        )

        self.partner = LaundryPartner.objects.create(
            name="Pressing Test Cancel",
        )

        self.job = PartnerJob.objects.create(
            code="JOB-CANCEL-TEST-001",
            order=self.order,
            partner=self.partner,
            status="received",
            notes="NOTE HISTORIQUE",
        )

    def test_cancel_partner_job_sets_status_and_timestamp(self):
        result = cancel_partner_job(
            partner_job=self.job,
            reason="Annulation client",
        )

        result.refresh_from_db()

        self.assertEqual(result.status, "canceled")
        self.assertIsNotNone(result.canceled_at)
        self.assertIn("ANNULATION: Annulation client", result.notes)

    def test_cancel_partner_job_preserves_existing_notes(self):
        result = cancel_partner_job(
            partner_job=self.job,
            reason="Client indisponible",
            notes="Annulation confirmée par OPS",
        )

        result.refresh_from_db()

        self.assertIn("NOTE HISTORIQUE", result.notes)
        self.assertIn("ANNULATION: Client indisponible", result.notes)
        self.assertIn("Annulation confirmée par OPS", result.notes)

    def test_cancel_partner_job_requires_reason(self):
        with self.assertRaises(ValueError):
            cancel_partner_job(
                partner_job=self.job,
                reason="   ",
            )

        self.job.refresh_from_db()

        self.assertEqual(self.job.status, "received")
        self.assertIsNone(self.job.canceled_at)

    def test_cancel_partner_job_is_idempotent(self):
        first = cancel_partner_job(
            partner_job=self.job,
            reason="Premier appel",
        )

        first.refresh_from_db()
        first_canceled_at = first.canceled_at
        first_notes = first.notes

        second = cancel_partner_job(
            partner_job=first,
            reason="Retry API",
        )

        second.refresh_from_db()

        self.assertEqual(second.status, "canceled")
        self.assertEqual(second.canceled_at, first_canceled_at)
        self.assertEqual(second.notes, first_notes)

    def test_handed_over_partner_job_cannot_be_canceled(self):
        self.job.status = "ready"
        self.job.save(update_fields=["status", "updated_at"])

        handover_partner_job(
            partner_job=self.job,
        )

        self.job.refresh_from_db()

        self.assertEqual(self.job.status, "handed_over")

        with self.assertRaises(ValueError):
            cancel_partner_job(
                partner_job=self.job,
                reason="Tentative tardive",
            )

    def test_canceled_partner_job_cannot_be_weighed(self):
        cancel_partner_job(
            partner_job=self.job,
            reason="Annulation avant pesée",
        )

        self.job.refresh_from_db()

        with self.assertRaises(ValueError):
            record_weighing(
                order=self.order,
                partner_job=self.job,
                net_weight="3.50",
                weighing_stage="partner_reception",
                performed_by_role="partner",
            )
