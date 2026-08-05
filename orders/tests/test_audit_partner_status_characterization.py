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
