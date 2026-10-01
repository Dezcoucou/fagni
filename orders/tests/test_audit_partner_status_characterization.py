"""
Audit parcours logistique V1 - Etape 2 : tests de caracterisation, aucune
correction de production. Couvre partner_update_status (item B, bugs A et B
du diagnostic).

Convention TestCase (pas TransactionTestCase) : alignee sur
test_bc3_return_driver_auto_assign.py, qui exerce deja ce meme endpoint avec
la meme mecanique (auto-affectation retour synchrone, pas de dependance a
transaction.on_commit ici).
"""
import json

import jwt
from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from orders.models import Customer, DeliveryLeg, Order
from partners.models import DeliveryPartner, LaundryPartner
from services.models import Service, ServiceCategory, ServiceExecution
from production.services import ensure_partner_job_for_pressing
from services.services import create_service_execution


RIVIERA_LAT = 5.360
RIVIERA_LNG = -3.950


def _token_partner(partner):
    return jwt.encode({'pid': partner.id, 'name': partner.name}, settings.SECRET_KEY, algorithm='HS256')


def _partner_headers(partner):
    return {'HTTP_AUTHORIZATION': f'Bearer {_token_partner(partner)}'}


def _make_laundry(phone="0700008101"):
    return LaundryPartner.objects.create(
        name="Pressing Audit", phone=phone, is_active=True,
        latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
    )


def _make_driver(phone):
    return DeliveryPartner.objects.create(
        name="Livreur Audit", phone=phone, is_active=True,
        latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
    )


def _make_order(laundry, phone):
    customer = Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")
    return Order.objects.create(
        customer=customer, laundry_partner=laundry, status="in_progress",
        pickup_address="Riviera 3", pickup_lat=RIVIERA_LAT, pickup_lng=RIVIERA_LNG,
        delivery_address="Riviera 3", delivery_lat=RIVIERA_LAT, delivery_lng=RIVIERA_LNG,
    )


def _set_status(laundry, order, status):
    return Client().post(
        reverse('api-partner-status', args=[order.id]),
        data=json.dumps({'status': status}),
        content_type='application/json',
        **_partner_headers(laundry),
    )


@override_settings(AUTO_ASSIGN_RETURN_DRIVER=False)
class PartnerUpdateStatusCharacterizationTests(TestCase):
    def test_pressing_v2_received_bag_starts_service_execution(self):
        category = ServiceCategory.objects.create(
            code="test-partner-v2-category",
            name="Test Partner V2 Category",
            is_active=True,
        )
        service = Service.objects.create(
            code="pressing_bag",
            category=category,
            name="Test Partner V2 Service",
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

        laundry = _make_laundry("0700008190")
        order = _make_order(laundry, "0700008090")

        execution = create_service_execution(
            order=order,
            service=service,
        )

        ensure_partner_job_for_pressing(
            order=order,
            partner=laundry,
            service_execution=execution,
        )

        resp = _set_status(laundry, order, "received_bag")

        self.assertEqual(resp.status_code, 200)

        execution.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_IN_PROGRESS,
        )
        self.assertIsNotNone(execution.started_at)
        self.assertEqual(order.status, "in_progress")

    def test_pressing_v2_ready_after_pickup_done_creates_return_leg(self):
        category = ServiceCategory.objects.create(
            code="test-v2-ready-category",
            name="Test V2 Ready Category",
            is_active=True,
        )
        service = Service.objects.create(
            code="pressing_bag",
            category=category,
            name="Test V2 Ready Service",
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

        laundry = _make_laundry("0700008191")
        pickup_driver = _make_driver("0700008192")
        order = _make_order(laundry, "0700008091")

        DeliveryLeg.objects.create(
            order=order,
            leg_type="pickup",
            driver=pickup_driver,
            status="done",
        )

        execution = create_service_execution(
            order=order,
            service=service,
        )
        execution.status = ServiceExecution.STATUS_IN_PROGRESS
        execution.save(update_fields=["status"])

        partner_job = ensure_partner_job_for_pressing(
            order=order,
            partner=laundry,
            service_execution=execution,
        )

        partner_job.status = "processing"
        partner_job.save(update_fields=["status"])

        resp = _set_status(laundry, order, "ready")

        self.assertEqual(resp.status_code, 200)

        order.refresh_from_db()
        execution.refresh_from_db()
        partner_job.refresh_from_db()

        self.assertEqual(partner_job.status, "ready")
        self.assertIsNotNone(order.wash_complete_time)

        return_legs = DeliveryLeg.objects.filter(
            order=order,
            leg_type="return",
        )
        self.assertEqual(return_legs.count(), 1)
        self.assertEqual(return_legs.first().status, "pending")
        self.assertIsNone(return_legs.first().driver_id)

    @override_settings(AUTO_ASSIGN_RETURN_DRIVER=True)
    def test_pressing_v2_ready_auto_assigns_return_driver(self):
        category = ServiceCategory.objects.create(
            code="test-v2-ready-auto-category",
            name="Test V2 Ready Auto Category",
            is_active=True,
        )
        service = Service.objects.create(
            code="pressing_bag",
            category=category,
            name="Test V2 Ready Auto Service",
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

        laundry = _make_laundry("0700008193")
        pickup_driver = _make_driver("0700008194")
        return_driver = _make_driver("0700008195")
        order = _make_order(laundry, "0700008092")

        DeliveryLeg.objects.create(
            order=order,
            leg_type="pickup",
            driver=pickup_driver,
            status="done",
        )

        execution = create_service_execution(
            order=order,
            service=service,
        )

        execution.status = ServiceExecution.STATUS_IN_PROGRESS
        execution.save(update_fields=["status"])

        partner_job = ensure_partner_job_for_pressing(
            order=order,
            partner=laundry,
            service_execution=execution,
        )

        partner_job.status = "processing"
        partner_job.save(update_fields=["status"])

        resp = _set_status(laundry, order, "ready")

        self.assertEqual(resp.status_code, 200)

        order.refresh_from_db()
        partner_job.refresh_from_db()

        self.assertEqual(partner_job.status, "ready")
        self.assertIsNotNone(order.wash_complete_time)

        return_legs = DeliveryLeg.objects.filter(
            order=order,
            leg_type="return",
        )
        self.assertEqual(return_legs.count(), 1)
        self.assertEqual(return_legs.first().status, "assigned")
        self.assertIsNotNone(return_legs.first().driver_id)

    def test_pressing_cannot_set_status_done_directly(self):
        laundry = _make_laundry("0700008102")
        order = _make_order(laundry, "0700008001")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", status="done")

        resp = _set_status(laundry, order, "done")

        order.refresh_from_db()
        self.assertNotEqual(
            order.status, "done",
            "le pressing ne doit jamais pouvoir mettre Order.status a 'done' directement",
        )
        self.assertNotEqual(resp.status_code, 200)

    def test_pressing_cannot_set_ready_before_pickup_done(self):
        laundry = _make_laundry("0700008103")
        pickup_driver = _make_driver("0700008108")
        order = _make_order(laundry, "0700008002")
        DeliveryLeg.objects.create(
            order=order, leg_type="pickup", driver=pickup_driver, status="in_progress",
        )

        resp = _set_status(laundry, order, "ready")

        order.refresh_from_db()
        self.assertNotEqual(resp.status_code, 200)
        self.assertNotEqual(order.status, "ready")
        self.assertIsNone(
            order.wash_complete_time,
            "aucun wash_complete_time ne doit etre pose si pickup n'est pas done",
        )
        self.assertFalse(
            DeliveryLeg.objects.filter(order=order, leg_type="return").exists(),
            "aucune jambe return ne doit etre creee si pickup n'est pas done",
        )

    def test_pressing_can_set_ready_after_pickup_done_flag_disabled(self):
        laundry = _make_laundry("0700008104")
        pickup_driver = _make_driver("0700008109")
        order = _make_order(laundry, "0700008003")
        DeliveryLeg.objects.create(
            order=order, leg_type="pickup", driver=pickup_driver, status="done",
        )

        resp = _set_status(laundry, order, "ready")

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "ready")
        self.assertIsNotNone(order.wash_complete_time)

        return_legs = DeliveryLeg.objects.filter(order=order, leg_type="return")
        self.assertEqual(return_legs.count(), 1)
        self.assertIsNone(return_legs.first().driver)
        self.assertEqual(return_legs.first().status, "pending")

    @override_settings(AUTO_ASSIGN_RETURN_DRIVER=True)
    def test_pressing_can_set_ready_after_pickup_done_flag_enabled_assigns_driver(self):
        laundry = _make_laundry("0700008105")
        pickup_driver = _make_driver("0700008106")
        return_driver = _make_driver("0700008107")
        order = _make_order(laundry, "0700008004")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=pickup_driver, status="done")

        resp = _set_status(laundry, order, "ready")

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "ready")
        self.assertIsNotNone(order.wash_complete_time)

        return_legs = DeliveryLeg.objects.filter(order=order, leg_type="return")
        self.assertEqual(return_legs.count(), 1)
        self.assertIsNotNone(return_legs.first().driver_id)
        self.assertEqual(return_legs.first().status, "assigned")

    def test_driver_handover_ready_partner_job(self):
        category = ServiceCategory.objects.create(
            code="test-handover-category",
            name="Test Handover Category",
            is_active=True,
        )
        service = Service.objects.create(
            code="pressing_article",
            category=category,
            name="Test Handover Service",
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
            pricing_mode="per_item",
            default_sla_hours=48,
        )

        laundry = _make_laundry("0700008201")
        driver = _make_driver("0700008202")
        order = _make_order(laundry, "0700008203")

        pickup = DeliveryLeg.objects.create(
            order=order,
            leg_type="pickup",
            driver=driver,
            status="done",
        )

        execution = create_service_execution(
            order=order,
            service=service,
        )

        execution.status = ServiceExecution.STATUS_IN_PROGRESS
        execution.save(update_fields=["status"])

        partner_job = ensure_partner_job_for_pressing(
            order=order,
            partner=laundry,
            service_execution=execution,
        )

        partner_job.status = "ready"
        partner_job.save(update_fields=["status"])

        order.wash_complete_time = order.created_at
        order.save(update_fields=["wash_complete_time"])

        return_leg = DeliveryLeg.objects.create(
            order=order,
            leg_type="return",
            driver=driver,
            status="in_progress",
        )

        from django.contrib.auth.models import User

        staff_user = User.objects.create_user(
            username="driver_handover_staff",
            password="test-password",
            is_staff=True,
        )

        client = Client()
        client.force_login(staff_user)

        resp = client.post(
            reverse(
                "orders:driver_leg_action",
                args=[return_leg.id, "handover"],
            ),
            data={"driver_id": driver.id},
        )

        self.assertEqual(resp.status_code, 302)

        partner_job.refresh_from_db()

        self.assertEqual(
            partner_job.status,
            "handed_over",
        )
