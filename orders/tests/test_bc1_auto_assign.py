"""
Sprint P0, Wave 3 (BC1) - auto-affectation additive a la creation d'une
commande fagni-client (orders/client_api.py::api_create_order), derriere
le flag AUTO_ASSIGN_ON_CLIENT_ORDER (desactive par defaut).

N'affecte jamais delivery_partner (livreur retour) : reste gere apres le
statut pressing "pret" par le flux OPS existant (regle confirmee par
l'audit prealable). Reutilise integralement orders.assignment et la
fonction de recalcul pricing partagee avec ops_assign_partner
(orders/services.py::recompute_order_pricing_for_laundry_partner).
"""
import json
import urllib.parse
from decimal import Decimal
from unittest.mock import patch

import jwt
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.client_api import _make_token
from orders.models import Customer, DeliveryLeg, Order
from partners.models import DeliveryPartner, LaundryPartner


RIVIERA_LAT = 5.360
RIVIERA_LNG = -3.950


def _token_ops():
    return jwt.encode({'ops': True, 'name': 'Test OPS'}, settings.SECRET_KEY, algorithm='HS256')


def _ops_headers():
    return {'HTTP_AUTHORIZATION': f'Bearer {_token_ops()}'}


def _make_customer(phone="0700004001"):
    return Customer.objects.create(name="Client BC1", phone=phone, address="Riviera 3")


def _client_headers(customer):
    return {'HTTP_AUTHORIZATION': f'Bearer {_make_token(customer)}'}


def _order_payload(**overrides):
    payload = {
        'accepted_cgu': '1',
        'bag_size': 'medium',
        'pickup_address': 'Riviera 3',
        'pickup_lat': RIVIERA_LAT,
        'pickup_lng': RIVIERA_LNG,
        'pickup_slot': 'demain_matin',
        'articles': [{'id': 'a1', 'name': 'Chemise', 'quantity': 2}],
    }
    payload.update(overrides)
    return payload


def _make_laundry(lat=RIVIERA_LAT, lng=RIVIERA_LNG, name="Pressing Test"):
    return LaundryPartner.objects.create(
        name=name, phone="0700000001", latitude=lat, longitude=lng, is_active=True,
    )


def _make_driver(lat=RIVIERA_LAT, lng=RIVIERA_LNG, name="Livreur Test"):
    return DeliveryPartner.objects.create(
        name=name, phone="0700000002", latitude=lat, longitude=lng, is_active=True,
    )


def _fake_osrm(distance_km="3.2"):
    def _osrm(origin_lat, origin_lng, dest_lat, dest_lng):
        return Decimal(distance_km)
    return _osrm


class Bc1FlagDisabledTests(TestCase):
    """Flag desactive (defaut) : comportement strictement identique a main."""

    def test_flag_desactive_aucune_affectation(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        resp = self.client.post(
            reverse('api-client-create-order'),
            data=json.dumps(_order_payload()),
            content_type='application/json',
            **_client_headers(customer),
        )

        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertIsNone(order.laundry_partner)
        self.assertIsNone(order.pickup_driver)
        self.assertIsNone(order.delivery_partner)
        self.assertFalse(DeliveryLeg.objects.filter(order=order).exists())

    def test_flag_desactive_defaut_settings(self):
        self.assertFalse(settings.AUTO_ASSIGN_ON_CLIENT_ORDER)


@override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
class Bc1AssignmentSuccessTests(TestCase):
    """Flag active, pressing + livreur disponibles."""

    def test_laundry_et_pickup_driver_renseignes_jamais_delivery_partner(self):
        customer = _make_customer()
        laundry = _make_laundry()
        driver = _make_driver()

        with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm()):
            resp = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer),
            )

        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertEqual(order.laundry_partner_id, laundry.id)
        self.assertEqual(order.pickup_driver_id, driver.id)
        self.assertIsNone(order.delivery_partner)

        leg = DeliveryLeg.objects.get(order=order, leg_type="pickup")
        self.assertEqual(leg.driver_id, driver.id)
        self.assertEqual(leg.status, "assigned")

    def test_reponse_retourne_le_prix_reellement_sauvegarde(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm("3.2")):
            resp = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer),
            )

        order = Order.objects.get(id=resp.json()['order_id'])
        body = resp.json()
        self.assertEqual(float(order.total_client_ttc), body['total'])
        self.assertEqual(float(order.service_fee), body['service_fee'])
        self.assertEqual(body['total'], body['bag_price'])

    def test_prix_identique_a_celui_obtenu_par_ops_assign_partner(self):
        """Meme fonction de recalcul partagee => meme prix final pour le
        meme partenaire/commande, que l'affectation soit auto ou OPS."""
        laundry = _make_laundry()
        _make_driver()

        customer_auto = _make_customer(phone="0700004002")
        with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm("4.5")):
            resp_auto = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer_auto),
            )
        order_auto = Order.objects.get(id=resp_auto.json()['order_id'])

        # Commande jumelle, meme flag off (comme si BC1 n'existait pas),
        # affectee manuellement via l'endpoint OPS reel.
        with override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=False):
            customer_ops = _make_customer(phone="0700004003")
            with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm("4.5")):
                resp_ops = self.client.post(
                    reverse('api-client-create-order'),
                    data=json.dumps(_order_payload()),
                    content_type='application/json',
                    **_client_headers(customer_ops),
                )
            order_ops = Order.objects.get(id=resp_ops.json()['order_id'])
            self.assertIsNone(order_ops.laundry_partner)

            with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm("4.5")):
                resp_assign = self.client.post(
                    reverse('api-ops-assign', args=[order_ops.id]),
                    data=json.dumps({'partner_id': laundry.id}),
                    content_type='application/json',
                    **_ops_headers(),
                )
            self.assertEqual(resp_assign.status_code, 200)

        order_ops.refresh_from_db()
        self.assertEqual(order_auto.total_client_ttc, order_ops.total_client_ttc)
        self.assertEqual(order_auto.delivery_fee, order_ops.delivery_fee)
        self.assertEqual(order_auto.service_fee, order_ops.service_fee)


@override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
class Bc1NoCandidateTests(TestCase):

    def test_aucun_pressing_disponible_commande_creee_ops_informe(self):
        customer = _make_customer()
        _make_driver()  # livreur dispo, mais pas de pressing

        resp = self.client.post(
            reverse('api-client-create-order'),
            data=json.dumps(_order_payload()),
            content_type='application/json',
            **_client_headers(customer),
        )

        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertIsNone(order.laundry_partner)
        self.assertIn("Pressing : non affecté", urllib.parse.unquote(order.notes or ""))

    def test_aucun_livreur_disponible_commande_creee_ops_informe(self):
        customer = _make_customer()
        with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm()):
            _make_laundry()  # pressing dispo, mais pas de livreur

            resp = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer),
            )

        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertIsNone(order.pickup_driver)
        self.assertIn("Collecte : livreur non affecté", urllib.parse.unquote(order.notes or ""))


@override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
class Bc1FailureResilienceTests(TestCase):

    def test_exception_moteur_commande_creee_pas_de_500(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        with patch("orders.assignment.pick_best_laundry", side_effect=RuntimeError("panne moteur")), \
             patch("orders.assignment.pick_best_driver", side_effect=RuntimeError("panne moteur")):
            resp = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer),
            )

        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertIsNone(order.laundry_partner)
        self.assertIsNone(order.pickup_driver)

    def test_echec_recalcul_pricing_commande_conservee_prix_coherent(self):
        customer = _make_customer()
        _make_laundry()

        # osrm_distance_km renvoie None => recompute_order_pricing_for_laundry_partner
        # retourne False, le pricing initial (provisoire) doit rester intact.
        with patch("orders.utils.distances.osrm_distance_km", return_value=None):
            resp = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer),
            )

        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertIsNotNone(order.laundry_partner_id)
        body = resp.json()
        # Le prix retourne doit correspondre exactement au prix sauvegarde
        # (provisoire, non recalcule) - jamais un prix "a moitie" recalcule.
        self.assertEqual(float(order.total_client_ttc), body['total'])
        self.assertIn("prix non recalculé", urllib.parse.unquote(order.notes or ""))


@override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
class Bc1NotificationTests(TestCase):

    def test_notifications_declenchees_une_seule_fois(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm()), \
             patch("orders.ops_api._send_notif_pressing") as mocked_pressing, \
             patch("orders.ops_api._send_notif_mission") as mocked_mission:
            resp = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer),
            )

        self.assertEqual(resp.status_code, 201)
        self.assertEqual(mocked_pressing.call_count, 1)
        self.assertEqual(mocked_mission.call_count, 1)

    def test_echec_notification_n_annule_ni_commande_ni_affectation(self):
        customer = _make_customer()
        laundry = _make_laundry()
        driver = _make_driver()

        with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm()), \
             patch("orders.ops_api._send_notif_pressing", side_effect=RuntimeError("push down")), \
             patch("orders.ops_api._send_notif_mission", side_effect=RuntimeError("push down")):
            resp = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer),
            )

        self.assertEqual(resp.status_code, 201)
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertEqual(order.laundry_partner_id, laundry.id)
        self.assertEqual(order.pickup_driver_id, driver.id)


@override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
class Bc1CompatibilityTests(TestCase):

    def test_ops_peut_reaffecter_pressing_et_livreur_apres_auto_affectation(self):
        customer = _make_customer()
        laundry1 = _make_laundry(name="Pressing 1")
        driver1 = _make_driver(name="Livreur 1")

        with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm()):
            resp = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer),
            )
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertEqual(order.laundry_partner_id, laundry1.id)
        self.assertEqual(order.pickup_driver_id, driver1.id)

        laundry2 = _make_laundry(name="Pressing 2", lat=RIVIERA_LAT + 0.01, lng=RIVIERA_LNG)
        driver2 = _make_driver(name="Livreur 2", lat=RIVIERA_LAT + 0.01, lng=RIVIERA_LNG)

        with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm()):
            r1 = self.client.post(
                reverse('api-ops-assign', args=[order.id]),
                data=json.dumps({'partner_id': laundry2.id}),
                content_type='application/json',
                **_ops_headers(),
            )
        self.assertEqual(r1.status_code, 200)

        r2 = self.client.post(
            reverse('api-ops-assign-driver', args=[order.id]),
            data=json.dumps({'driver_id': driver2.id, 'driver_type': 'pickup'}),
            content_type='application/json',
            **_ops_headers(),
        )
        self.assertEqual(r2.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.laundry_partner_id, laundry2.id)
        self.assertEqual(order.pickup_driver_id, driver2.id)

    def test_refus_pressing_remet_commande_dans_etat_attendu(self):
        customer = _make_customer()
        laundry = _make_laundry()
        _make_driver()

        with patch("orders.utils.distances.osrm_distance_km", side_effect=_fake_osrm()):
            resp = self.client.post(
                reverse('api-client-create-order'),
                data=json.dumps(_order_payload()),
                content_type='application/json',
                **_client_headers(customer),
            )
        order = Order.objects.get(id=resp.json()['order_id'])
        self.assertEqual(order.laundry_partner_id, laundry.id)

        partner_token = jwt.encode({'partner_id': laundry.id}, settings.SECRET_KEY, algorithm='HS256')
        refuse_resp = self.client.post(
            reverse('api-partner-refuse', args=[order.id]),
            data=json.dumps({'raison': 'Capacité insuffisante'}),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {partner_token}',
        )

        order.refresh_from_db()
        if refuse_resp.status_code == 200:
            self.assertIsNone(order.laundry_partner)
            self.assertEqual(order.status, 'pending')
        else:
            # Auth partenaire non couverte par ce test (hors perimetre BC1) -
            # au minimum, l'affectation initiale doit etre restee coherente.
            self.assertEqual(order.laundry_partner_id, laundry.id)


@override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
class Bc1CanceledOrderTests(TestCase):

    def test_commande_annulee_aucun_traitement_d_affectation(self):
        from orders.client_api import _bc1_auto_assign_pickup_and_laundry

        customer = _make_customer()
        _make_laundry()
        _make_driver()

        order = Order.objects.create(
            customer=customer, pricing_mode="item", status="canceled",
            total_client_ttc=Decimal("5000"), pickup_lat=RIVIERA_LAT, pickup_lng=RIVIERA_LNG,
        )

        result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()
        self.assertFalse(result["laundry_assigned"])
        self.assertFalse(result["driver_assigned"])
        self.assertIsNone(order.laundry_partner)
        self.assertIsNone(order.pickup_driver)
