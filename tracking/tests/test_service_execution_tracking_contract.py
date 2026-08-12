from django.core.exceptions import ValidationError
from django.test import TestCase

from logistics.models import Mission
from orders.models import Customer, Order
from partners.models import LaundryPartner
from production.models import PartnerJob
from services.models import Service, ServiceCategory, ServiceExecution
from tracking.models import Incident, TrackingEvent
from tracking.services import create_incident, create_tracking_event


class TrackingServiceExecutionContractTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="test-tracking-category",
            name="Test Tracking Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="test-tracking-service",
            category=self.category,
            name="Test Tracking Service",
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

        self.customer_a = Customer.objects.create(
            name="Client Tracking A",
            phone="0700004001",
        )

        self.customer_b = Customer.objects.create(
            name="Client Tracking B",
            phone="0700004002",
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

        self.partner = LaundryPartner.objects.create(
            name="Blanchisserie Test Tracking",
        )

        self.mission_a = Mission.objects.create(
            code="MSN-TRACK-A",
            order=self.order_a,
            service_execution=self.execution_a,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        self.partner_job_a = PartnerJob.objects.create(
            code="JOB-TRACK-A",
            order=self.order_a,
            service_execution=self.execution_a,
            partner=self.partner,
        )

    def test_legacy_tracking_event_without_service_execution_is_allowed(self):
        event = TrackingEvent.objects.create(
            order=self.order_a,
            mission=self.mission_a,
            event_type="mission_started",
            title="Legacy event",
        )

        self.assertIsNone(event.service_execution)

    def test_tracking_event_with_same_execution_is_allowed(self):
        event = create_tracking_event(
            order=self.order_a,
            service_execution=self.execution_a,
            mission=self.mission_a,
            partner_job=self.partner_job_a,
            event_type="mission_started",
            title="Tracking event",
        )

        self.assertEqual(
            event.service_execution_id,
            self.execution_a.id,
        )

    def test_tracking_event_cross_order_is_rejected_by_model(self):
        event = TrackingEvent(
            order=self.order_b,
            service_execution=self.execution_a,
            event_type="mission_started",
            title="Invalid tracking event",
        )

        with self.assertRaises(ValidationError):
            event.save()

    def test_tracking_service_rejects_cross_order_execution(self):
        with self.assertRaises(ValueError):
            create_tracking_event(
                order=self.order_b,
                service_execution=self.execution_a,
                event_type="mission_started",
                title="Invalid tracking event",
            )

    def test_tracking_service_rejects_mission_from_other_execution(self):
        mission_b = Mission.objects.create(
            code="MSN-TRACK-B",
            order=self.order_b,
            service_execution=self.execution_b,
            mission_type="pickup_from_customer",
            status="assigned",
        )

        with self.assertRaises(ValueError):
            create_tracking_event(
                order=self.order_a,
                service_execution=self.execution_a,
                mission=mission_b,
                event_type="mission_started",
                title="Invalid tracking event",
            )

    def test_tracking_service_rejects_partner_job_from_other_execution(self):
        partner_job_b = PartnerJob.objects.create(
            code="JOB-TRACK-B",
            order=self.order_b,
            service_execution=self.execution_b,
            partner=self.partner,
        )

        with self.assertRaises(ValueError):
            create_tracking_event(
                order=self.order_a,
                service_execution=self.execution_a,
                partner_job=partner_job_b,
                event_type="mission_started",
                title="Invalid tracking event",
            )

    def test_legacy_incident_without_service_execution_is_allowed(self):
        incident = Incident.objects.create(
            order=self.order_a,
            mission=self.mission_a,
            incident_type="delay",
            title="Legacy incident",
            description="Test",
        )

        self.assertIsNone(incident.service_execution)

    def test_incident_with_same_execution_is_allowed(self):
        incident = create_incident(
            order=self.order_a,
            service_execution=self.execution_a,
            mission=self.mission_a,
            partner_job=self.partner_job_a,
            incident_type="delay",
            title="Incident",
            description="Test incident",
        )

        self.assertEqual(
            incident.service_execution_id,
            self.execution_a.id,
        )

    def test_incident_cross_order_is_rejected_by_model(self):
        incident = Incident(
            order=self.order_b,
            service_execution=self.execution_a,
            incident_type="delay",
            title="Invalid incident",
            description="Test",
        )

        with self.assertRaises(ValidationError):
            incident.save()

    def test_incident_service_rejects_cross_order_execution(self):
        with self.assertRaises(ValueError):
            create_incident(
                order=self.order_b,
                service_execution=self.execution_a,
                incident_type="delay",
                title="Invalid incident",
                description="Test",
            )
