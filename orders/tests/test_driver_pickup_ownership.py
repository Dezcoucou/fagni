"""
Audit de stabilite (lot 3) - driver_confirm_pickup n'avait aucune
verification de proprietaire : si aucune DeliveryLeg pickup n'existait
pour le livreur appelant, le code retombait sur N'IMPORTE QUELLE jambe
pickup de la commande et reecrivait son driver sans condition. Un livreur
dont la mission avait ete reaffectee (OPS ou BC1) pouvait donc, depuis un
ecran reste ouvert, reprendre silencieusement la collecte a la place du
livreur reellement affecte.

Corrige en refusant (403) la confirmation si la jambe pickup existante
appartient a un AUTRE livreur, avant tout effet de bord (ajustement
wallet, notes, articles_count).
"""
import json

import jwt
from django.conf import settings
from django.test import TestCase

from orders.models import Customer, DeliveryLeg, Order
from partners.models import DeliveryPartner, LaundryPartner


RIVIERA_LAT = 5.360
RIVIERA_LNG = -3.950


def _token_driver(driver_id):
    return jwt.encode({'did': driver_id, 'name': 'Test'}, settings.SECRET_KEY, algorithm='HS256')


def _headers(driver_id):
    return {'HTTP_AUTHORIZATION': f'Bearer {_token_driver(driver_id)}'}


def _make_order(phone="0700008001"):
    laundry = LaundryPartner.objects.create(name="Pressing", phone="0700008000", is_active=True)
    customer = Customer.objects.create(name="Client Test", phone=phone, address="Riviera 3")
    return Order.objects.create(
        customer=customer, laundry_partner=laundry, status="in_progress",
        pickup_address="Riviera 3", pickup_lat=RIVIERA_LAT, pickup_lng=RIVIERA_LNG,
        delivery_address="Riviera 3", delivery_lat=RIVIERA_LAT, delivery_lng=RIVIERA_LNG,
    )


class DriverConfirmPickupOwnershipTests(TestCase):
    def test_livreur_reellement_affecte_peut_confirmer(self):
        driver = DeliveryPartner.objects.create(name="Livreur A", phone="0700008010", is_active=True)
        order = _make_order()
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="assigned")

        resp = self.client.post(
            f"/api/driver/orders/{order.id}/pickup/",
            data=json.dumps({"articles_count": 3}),
            content_type="application/json",
            **_headers(driver.id),
        )
        self.assertEqual(resp.status_code, 200)

        leg = DeliveryLeg.objects.get(order=order, leg_type="pickup")
        self.assertEqual(leg.driver_id, driver.id)
        self.assertEqual(leg.status, "in_progress")

    def test_livreur_non_affecte_ne_peut_pas_reprendre_la_mission_dun_autre(self):
        """Le coeur du bug : la mission a ete affectee/reaffectee a B, A ne doit jamais pouvoir la reprendre."""
        driver_b = DeliveryPartner.objects.create(name="Livreur B (affecte)", phone="0700008011", is_active=True)
        driver_a = DeliveryPartner.objects.create(name="Livreur A (intrus)", phone="0700008012", is_active=True)
        order = _make_order(phone="0700008002")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver_b, status="assigned")

        resp = self.client.post(
            f"/api/driver/orders/{order.id}/pickup/",
            data=json.dumps({"articles_count": 3}),
            content_type="application/json",
            **_headers(driver_a.id),
        )
        self.assertEqual(resp.status_code, 403)

        # La jambe doit rester intacte, toujours affectee a B, jamais demarree par A.
        leg = DeliveryLeg.objects.get(order=order, leg_type="pickup")
        self.assertEqual(leg.driver_id, driver_b.id)
        self.assertEqual(leg.status, "assigned")

        # Aucun effet de bord (notes, articles_count) ne doit avoir ete applique.
        order.refresh_from_db()
        self.assertNotIn("COLLECTE:", order.notes or "")

    def test_livreur_peut_reclamer_une_jambe_pickup_non_affectee(self):
        """Cas legitime : jambe pickup existante mais sans driver (fallback manuel OPS)."""
        driver = DeliveryPartner.objects.create(name="Livreur A", phone="0700008013", is_active=True)
        order = _make_order(phone="0700008003")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=None, status="pending")

        resp = self.client.post(
            f"/api/driver/orders/{order.id}/pickup/",
            data=json.dumps({"articles_count": 2}),
            content_type="application/json",
            **_headers(driver.id),
        )
        self.assertEqual(resp.status_code, 200)

        leg = DeliveryLeg.objects.get(order=order, leg_type="pickup")
        self.assertEqual(leg.driver_id, driver.id)
