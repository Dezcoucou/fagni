"""
Mission BC3 - auto-affectation additive du livreur retour, au moment ou le
pressing marque la commande PRET (orders/partner_api.py::partner_update_status,
endpoint reellement appele par l'app fagni_partner : POST
/api/partner/orders/<id>/status/ {status: 'ready'}), derriere le flag
AUTO_ASSIGN_RETURN_DRIVER (desactive par defaut).

Cause racine (production) : ce point de code creait deja la DeliveryLeg
return (get_or_create), mais n'appelait jamais pick_best_driver et
n'assignait donc jamais order.delivery_partner - la mission retour restait
visible dans OPS mais en permanence non affectee, quel que soit le nombre
de livreurs actifs, exactement le symptome rapporte.

Reutilise integralement pick_best_driver (meme moteur que BC1 collecte et
que ops_assign_return_driver) et reproduit a l'identique les effets de bord
de ops_assign_return_driver (DeliveryLeg.driver/status, order.delivery_partner,
order.cost_driver_delivery, notifications) - aucune nouvelle regle metier,
aucun nouveau canal de notification.
"""
import json
from decimal import Decimal
from unittest.mock import patch

import jwt
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.models import Customer, DeliveryLeg, Order
from partners.models import DeliveryPartner, LaundryPartner


RIVIERA_LAT = 5.360
RIVIERA_LNG = -3.950


def _token_ops():
    return jwt.encode({'ops': True, 'name': 'Test OPS'}, settings.SECRET_KEY, algorithm='HS256')


def _ops_headers():
    return {'HTTP_AUTHORIZATION': f'Bearer {_token_ops()}'}


def _token_partner(partner):
    return jwt.encode({'pid': partner.id, 'name': partner.name}, settings.SECRET_KEY, algorithm='HS256')


def _partner_headers(partner):
    return {'HTTP_AUTHORIZATION': f'Bearer {_token_partner(partner)}'}


def _make_laundry(name="Pressing BC3"):
    return LaundryPartner.objects.create(
        name=name, phone="0700000101", is_active=True,
        latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
    )


def _make_driver(name="Livreur Retour BC3", active=True):
    return DeliveryPartner.objects.create(
        name=name, phone="0700000102", is_active=active,
        latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
    )


def _make_order(laundry, phone="0700004101"):
    customer = Customer.objects.create(name="Client BC3", phone=phone, address="Riviera 3")
    return Order.objects.create(
        customer=customer,
        laundry_partner=laundry,
        status="in_progress",
        pickup_address="Riviera 3",
        pickup_lat=RIVIERA_LAT,
        pickup_lng=RIVIERA_LNG,
        delivery_address="Riviera 3",
        delivery_lat=RIVIERA_LAT,
        delivery_lng=RIVIERA_LNG,
    )


def _mark_ready(laundry, order):
    return _client().post(
        reverse('api-partner-status', args=[order.id]),
        data=json.dumps({'status': 'ready'}),
        content_type='application/json',
        **_partner_headers(laundry),
    )


def _client():
    from django.test import Client
    return Client()


class Bc3FlagDisabledTests(TestCase):
    """Flag desactive (defaut) : comportement strictement identique a avant BC3."""

    def test_flag_desactive_mission_retour_creee_mais_non_affectee(self):
        laundry = _make_laundry()
        _make_driver()
        order = _make_order(laundry)

        resp = _mark_ready(laundry, order)

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertIsNotNone(order.wash_complete_time)
        self.assertIsNone(order.delivery_partner)
        leg = DeliveryLeg.objects.get(order=order, leg_type="return")
        self.assertIsNone(leg.driver)
        self.assertEqual(leg.status, "pending")

    def test_flag_desactive_defaut_settings(self):
        self.assertFalse(settings.AUTO_ASSIGN_RETURN_DRIVER)


@override_settings(AUTO_ASSIGN_RETURN_DRIVER=True)
class Bc3FlagEnabledTests(TestCase):
    """Flag active : symptome production reproduit puis corrige."""

    def test_statut_pret_avec_livreur_disponible_affecte_automatiquement(self):
        laundry = _make_laundry()
        driver = _make_driver()
        order = _make_order(laundry)

        resp = _mark_ready(laundry, order)

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.delivery_partner_id, driver.id)
        self.assertIsNotNone(order.cost_driver_delivery)

        leg = DeliveryLeg.objects.get(order=order, leg_type="return")
        self.assertEqual(leg.driver_id, driver.id)
        self.assertEqual(leg.status, "assigned")

        # Aucune 2e jambe return
        self.assertEqual(DeliveryLeg.objects.filter(order=order, leg_type="return").count(), 1)

    def test_aucun_livreur_actif_mission_retour_creee_mais_non_assignee(self):
        laundry = _make_laundry()
        # Aucun DeliveryPartner actif.
        order = _make_order(laundry)

        resp = _mark_ready(laundry, order)

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertIsNone(order.delivery_partner)
        leg = DeliveryLeg.objects.get(order=order, leg_type="return")
        self.assertIsNone(leg.driver)
        self.assertEqual(leg.status, "pending")

    def test_mode_manuel_aucune_auto_affectation(self):
        from orders.config_models import AssignmentSettings

        laundry = _make_laundry()
        _make_driver()
        order = _make_order(laundry)

        cfg = AssignmentSettings.get_solo()
        cfg.driver_assignment_mode = "manual"
        cfg.save()

        resp = _mark_ready(laundry, order)

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertIsNone(order.delivery_partner)
        leg = DeliveryLeg.objects.get(order=order, leg_type="return")
        self.assertIsNone(leg.driver)

    def test_reaffectation_ops_toujours_possible_apres_auto_affectation(self):
        laundry = _make_laundry()
        driver1 = _make_driver(name="Livreur 1")
        driver2 = DeliveryPartner.objects.create(
            name="Livreur 2", phone="0700000103", is_active=True,
            latitude=RIVIERA_LAT, longitude=RIVIERA_LNG,
        )
        order = _make_order(laundry)

        _mark_ready(laundry, order)
        order.refresh_from_db()
        self.assertEqual(order.delivery_partner_id, driver1.id)

        reassign_resp = _client().post(
            f"/api/ops/orders/{order.id}/assign-return-driver/",
            data=json.dumps({'driver_id': driver2.id}),
            content_type='application/json',
            **_ops_headers(),
        )
        self.assertEqual(reassign_resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.delivery_partner_id, driver2.id)

    def test_repetition_statut_pret_aucune_mission_doublon_aucune_double_notification(self):
        laundry = _make_laundry()
        _make_driver()
        order = _make_order(laundry)

        with patch("orders.ops_api._send_notif_mission") as mocked_notif:
            _mark_ready(laundry, order)
            first_call_count = mocked_notif.call_count
            # Repetition du meme statut "ready".
            resp2 = _mark_ready(laundry, order)

        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(mocked_notif.call_count, first_call_count, "aucune notification supplementaire sur repetition")
        self.assertEqual(DeliveryLeg.objects.filter(order=order, leg_type="return").count(), 1)

    def test_echec_moteur_ne_bloque_jamais_la_transition_pret(self):
        laundry = _make_laundry()
        _make_driver()
        order = _make_order(laundry)

        with patch("orders.assignment.pick_best_driver", side_effect=Exception("boom")):
            resp = _mark_ready(laundry, order)

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertIsNotNone(order.wash_complete_time, "le passage a PRET doit reussir meme si BC3 echoue")

    def test_ops_peut_toujours_affecter_manuellement_sans_bc3(self):
        """Preuve du garde-fou : le fallback OPS n'est jamais retire, meme flag actif."""
        laundry = _make_laundry()
        driver = _make_driver()
        order = _make_order(laundry)
        # Pas d'appel a mark_ready ici : on verifie juste que l'endpoint OPS
        # fonctionne independamment de BC3 des que wash_complete_time existe.
        from django.utils import timezone
        Order.objects.filter(pk=order.pk).update(wash_complete_time=timezone.now())

        resp = _client().post(
            f"/api/ops/orders/{order.id}/assign-return-driver/",
            data=json.dumps({'driver_id': driver.id}),
            content_type='application/json',
            **_ops_headers(),
        )
        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.delivery_partner_id, driver.id)
