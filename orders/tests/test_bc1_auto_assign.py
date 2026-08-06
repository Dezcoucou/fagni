"""
Tests BC1 — affectation des ressources après paiement confirmé.

Règle métier FAGNI :
- la création d'une commande client ne mobilise jamais immédiatement
  un pressing ou un livreur ;
- le flag AUTO_ASSIGN_ON_CLIENT_ORDER ne doit pas contourner cette règle ;
- le helper BC1 refuse toute commande non payée ou annulée ;
- l'affectation devient possible uniquement après confirmation comptable
  réelle du paiement ;
- BC1 affecte le pressing et le livreur de collecte uniquement ;
- le livreur retour, delivery_partner, reste géré par le workflow retour.
"""

import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse

from orders.client_api import (
    _bc1_auto_assign_pickup_and_laundry,
    _make_token,
)
from orders.models import Customer, DeliveryLeg, Order, Payment
from partners.models import DeliveryPartner, LaundryPartner


RIVIERA_LAT = 5.360
RIVIERA_LNG = -3.950


def _make_customer(phone="0700004001"):
    return Customer.objects.create(
        name="Client BC1",
        phone=phone,
        address="Riviera 3",
    )


def _client_headers(customer):
    return {
        "HTTP_AUTHORIZATION": f"Bearer {_make_token(customer)}",
    }


def _order_payload(**overrides):
    payload = {
        "accepted_cgu": "1",
        "bag_size": "medium",
        "pickup_address": "Riviera 3",
        "pickup_lat": RIVIERA_LAT,
        "pickup_lng": RIVIERA_LNG,
        "pickup_slot": "demain_matin",
        "articles": [
            {
                "id": "a1",
                "name": "Chemise",
                "quantity": 2,
            },
        ],
    }
    payload.update(overrides)
    return payload


def _make_laundry(
    *,
    lat=RIVIERA_LAT,
    lng=RIVIERA_LNG,
    name="Pressing Test",
    phone="0700000001",
):
    return LaundryPartner.objects.create(
        name=name,
        phone=phone,
        latitude=lat,
        longitude=lng,
        is_active=True,
    )


def _make_driver(
    *,
    lat=RIVIERA_LAT,
    lng=RIVIERA_LNG,
    name="Livreur Test",
    phone="0700000002",
):
    return DeliveryPartner.objects.create(
        name=name,
        phone=phone,
        latitude=lat,
        longitude=lng,
        is_active=True,
    )


def _fake_osrm(distance_km="3.2"):
    def _osrm(origin_lat, origin_lng, dest_lat, dest_lng):
        return Decimal(distance_km)

    return _osrm


def _make_direct_order(
    customer,
    *,
    status="pending",
    payment_status="pending",
    amount_paid=Decimal("0"),
    total=Decimal("10000"),
    pickup_lat=RIVIERA_LAT,
    pickup_lng=RIVIERA_LNG,
):
    return Order.objects.create(
        customer=customer,
        status=status,
        payment_status=payment_status,
        amount_paid=amount_paid,
        total_client_ttc=total,
        total=total,
        is_draft=False,
        pricing_mode="item",
        pickup_address="Riviera 3",
        delivery_address="Riviera 3",
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
        delivery_lat=pickup_lat,
        delivery_lng=pickup_lng,
    )


def _record_full_payment(order, reference=None):
    """
    Crée une vraie ligne Payment puis resynchronise la commande depuis
    la source de vérité comptable.
    """
    reference = reference or f"BC1-PAID-{order.id}"

    Payment.objects.create(
        order=order,
        amount=int(Decimal(str(order.total_client_ttc))),
        channel="test",
        reference=reference,
    )

    order.sync_payment_status_from_payments(save=True)
    order.refresh_from_db()

    return order


@override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=False)
class ClientOrderCreationWithFlagDisabledTests(TestCase):
    def test_creation_ne_mobilise_aucune_ressource(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        response = self.client.post(
            reverse("api-client-create-order"),
            data=json.dumps(_order_payload()),
            content_type="application/json",
            **_client_headers(customer),
        )

        self.assertEqual(response.status_code, 201)

        order = Order.objects.get(pk=response.json()["order_id"])

        self.assertIsNone(order.laundry_partner_id)
        self.assertIsNone(order.pickup_driver_id)
        self.assertIsNone(order.delivery_partner_id)
        self.assertFalse(
            DeliveryLeg.objects.filter(order=order).exists()
        )


@override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
class ClientOrderCreationWithFlagEnabledTests(TestCase):
    def test_flag_active_ne_contourne_pas_le_garde_paiement(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        with patch(
            "orders.utils.distances.osrm_distance_km",
            side_effect=_fake_osrm(),
        ):
            response = self.client.post(
                reverse("api-client-create-order"),
                data=json.dumps(_order_payload()),
                content_type="application/json",
                **_client_headers(customer),
            )

        self.assertEqual(response.status_code, 201)

        order = Order.objects.get(pk=response.json()["order_id"])

        self.assertNotEqual(order.payment_status, "paid")
        self.assertEqual(order.amount_paid, Decimal("0"))

        self.assertIsNone(order.laundry_partner_id)
        self.assertIsNone(order.pickup_driver_id)
        self.assertIsNone(order.delivery_partner_id)

        self.assertFalse(
            DeliveryLeg.objects.filter(order=order).exists(),
            (
                "La création d'une commande non payée ne doit créer "
                "aucune mission ou jambe affectée."
            ),
        )

    def test_creation_ne_declenche_pas_les_moteurs_affectation(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        with patch(
            "orders.assignment.pick_best_laundry"
        ) as mocked_laundry, patch(
            "orders.assignment.pick_best_driver"
        ) as mocked_driver:
            response = self.client.post(
                reverse("api-client-create-order"),
                data=json.dumps(_order_payload()),
                content_type="application/json",
                **_client_headers(customer),
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(mocked_laundry.call_count, 0)
        self.assertEqual(mocked_driver.call_count, 0)


class Bc1UnpaidOrderGateTests(TestCase):
    def test_helper_refuse_commande_non_payee(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        order = _make_direct_order(customer)

        with patch(
            "orders.assignment.pick_best_laundry"
        ) as mocked_laundry, patch(
            "orders.assignment.pick_best_driver"
        ) as mocked_driver:
            result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()

        self.assertEqual(
            result,
            {
                "laundry_assigned": False,
                "driver_assigned": False,
                "pricing_recomputed": False,
            },
        )

        self.assertEqual(mocked_laundry.call_count, 0)
        self.assertEqual(mocked_driver.call_count, 0)

        self.assertIsNone(order.laundry_partner_id)
        self.assertIsNone(order.pickup_driver_id)
        self.assertIsNone(order.delivery_partner_id)
        self.assertFalse(
            DeliveryLeg.objects.filter(order=order).exists()
        )

    def test_amount_paid_manuel_sans_payment_ne_suffit_pas(self):
        """
        Modifier uniquement amount_paid/payment_status ne doit pas contourner
        la source de vérité Payment.
        """
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        order = _make_direct_order(
            customer,
            payment_status="paid",
            amount_paid=Decimal("10000"),
            total=Decimal("10000"),
        )

        self.assertEqual(
            Payment.objects.filter(order=order).count(),
            0,
        )

        result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()

        self.assertFalse(result["laundry_assigned"])
        self.assertFalse(result["driver_assigned"])
        self.assertIsNone(order.laundry_partner_id)
        self.assertIsNone(order.pickup_driver_id)
        self.assertFalse(
            DeliveryLeg.objects.filter(order=order)
            .exclude(status__in=("pending", "canceled"))
            .exists(),
            (
                "un faux paiement sans ligne Payment ne doit activer "
                "aucune jambe logistique"
            ),
        )
        self.assertFalse(
            DeliveryLeg.objects.filter(order=order)
            .exclude(driver_id=None)
            .exists(),
            (
                "un faux paiement sans ligne Payment ne doit affecter "
                "aucun livreur"
            ),
        )


class Bc1CanceledOrderGateTests(TestCase):
    def test_commande_annulee_refusee_meme_si_payment_existe(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        order = _make_direct_order(
            customer,
            status="canceled",
        )
        _record_full_payment(order)

        order.status = "canceled"
        order.save(update_fields=["status"])

        result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()

        self.assertFalse(result["laundry_assigned"])
        self.assertFalse(result["driver_assigned"])
        self.assertIsNone(order.laundry_partner_id)
        self.assertIsNone(order.pickup_driver_id)
        self.assertIsNone(order.delivery_partner_id)
        self.assertFalse(
            DeliveryLeg.objects.filter(order=order).exists()
        )


class Bc1PaidOrderAssignmentTests(TestCase):
    def test_paiement_confirme_autorise_pressing_et_collecte(self):
        customer = _make_customer()
        laundry = _make_laundry()
        driver = _make_driver()

        order = _make_direct_order(customer)
        _record_full_payment(order)

        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(
            order.amount_paid,
            order.total_client_ttc,
        )

        with patch(
            "orders.utils.distances.osrm_distance_km",
            side_effect=_fake_osrm(),
        ):
            result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()

        self.assertTrue(result["laundry_assigned"])
        self.assertTrue(result["driver_assigned"])

        self.assertEqual(
            order.laundry_partner_id,
            laundry.id,
        )
        self.assertEqual(
            order.pickup_driver_id,
            driver.id,
        )

        self.assertIsNone(
            order.delivery_partner_id,
            (
                "BC1 ne doit jamais affecter le livreur retour au moment "
                "de la collecte."
            ),
        )

        pickup_leg = DeliveryLeg.objects.get(
            order=order,
            leg_type="pickup",
        )

        self.assertEqual(pickup_leg.driver_id, driver.id)
        self.assertEqual(pickup_leg.status, "assigned")

        self.assertFalse(
            DeliveryLeg.objects.filter(
                order=order,
                leg_type="return",
                status="assigned",
            ).exists()
        )

    def test_helper_est_idempotent_sur_une_commande_deja_affectee(self):
        customer = _make_customer()
        laundry = _make_laundry()
        driver = _make_driver()

        order = _make_direct_order(customer)
        _record_full_payment(order)

        with patch(
            "orders.utils.distances.osrm_distance_km",
            side_effect=_fake_osrm(),
        ):
            first_result = _bc1_auto_assign_pickup_and_laundry(order)
            second_result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()

        self.assertTrue(first_result["laundry_assigned"])
        self.assertTrue(first_result["driver_assigned"])
        self.assertTrue(second_result["laundry_assigned"])
        self.assertTrue(second_result["driver_assigned"])

        self.assertEqual(
            order.laundry_partner_id,
            laundry.id,
        )
        self.assertEqual(
            order.pickup_driver_id,
            driver.id,
        )

        self.assertEqual(
            DeliveryLeg.objects.filter(
                order=order,
                leg_type="pickup",
            ).count(),
            1,
        )

    def test_notifications_declenchees_apres_paiement_uniquement(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        order = _make_direct_order(customer)

        with patch(
            "orders.ops_api._send_notif_pressing"
        ) as mocked_pressing, patch(
            "orders.ops_api._send_notif_mission"
        ) as mocked_mission:
            unpaid_result = _bc1_auto_assign_pickup_and_laundry(order)

        self.assertFalse(unpaid_result["laundry_assigned"])
        self.assertFalse(unpaid_result["driver_assigned"])
        self.assertEqual(mocked_pressing.call_count, 0)
        self.assertEqual(mocked_mission.call_count, 0)

        _record_full_payment(order)

        with patch(
            "orders.utils.distances.osrm_distance_km",
            side_effect=_fake_osrm(),
        ), patch(
            "orders.ops_api._send_notif_pressing"
        ) as mocked_pressing, patch(
            "orders.ops_api._send_notif_mission"
        ) as mocked_mission:
            paid_result = _bc1_auto_assign_pickup_and_laundry(order)

        self.assertTrue(paid_result["laundry_assigned"])
        self.assertTrue(paid_result["driver_assigned"])
        self.assertEqual(mocked_pressing.call_count, 1)
        self.assertEqual(mocked_mission.call_count, 1)


class Bc1PaidOrderCandidateTests(TestCase):
    def test_paiement_confirme_sans_pressing_ne_casse_pas(self):
        customer = _make_customer()
        _make_driver()

        order = _make_direct_order(customer)
        _record_full_payment(order)

        result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()

        self.assertFalse(result["laundry_assigned"])
        self.assertIsNone(order.laundry_partner_id)

    def test_paiement_confirme_sans_livreur_ne_casse_pas(self):
        customer = _make_customer()
        laundry = _make_laundry()

        order = _make_direct_order(customer)
        _record_full_payment(order)

        with patch(
            "orders.utils.distances.osrm_distance_km",
            side_effect=_fake_osrm(),
        ):
            result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()

        self.assertTrue(result["laundry_assigned"])
        self.assertFalse(result["driver_assigned"])

        self.assertEqual(
            order.laundry_partner_id,
            laundry.id,
        )
        self.assertIsNone(order.pickup_driver_id)
        self.assertFalse(
            DeliveryLeg.objects.filter(order=order).exists()
        )

    def test_absence_coordonnees_ne_permet_pas_affectation_livreur(self):
        customer = _make_customer()
        laundry = _make_laundry()
        _make_driver()

        order = _make_direct_order(
            customer,
            pickup_lat=None,
            pickup_lng=None,
        )
        _record_full_payment(order)

        result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()

        self.assertTrue(result["laundry_assigned"])
        self.assertFalse(result["driver_assigned"])

        self.assertEqual(
            order.laundry_partner_id,
            laundry.id,
        )
        self.assertIsNone(order.pickup_driver_id)
        self.assertFalse(
            DeliveryLeg.objects.filter(order=order).exists()
        )


class Bc1PaymentSourceOfTruthTests(TestCase):
    def test_paiement_partiel_ne_declenche_pas_affectation(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        order = _make_direct_order(
            customer,
            total=Decimal("10000"),
        )

        Payment.objects.create(
            order=order,
            amount=4000,
            channel="test",
            reference=f"BC1-PARTIAL-{order.id}",
        )
        order.sync_payment_status_from_payments(save=True)
        order.refresh_from_db()

        self.assertEqual(order.payment_status, "partial")
        self.assertEqual(order.amount_paid, Decimal("4000"))

        result = _bc1_auto_assign_pickup_and_laundry(order)

        order.refresh_from_db()

        self.assertFalse(result["laundry_assigned"])
        self.assertFalse(result["driver_assigned"])
        self.assertIsNone(order.laundry_partner_id)
        self.assertIsNone(order.pickup_driver_id)

    def test_paiement_total_depuis_payment_est_accepte(self):
        customer = _make_customer()
        _make_laundry()
        _make_driver()

        order = _make_direct_order(
            customer,
            total=Decimal("10000"),
        )
        _record_full_payment(order)

        with patch(
            "orders.utils.distances.osrm_distance_km",
            side_effect=_fake_osrm(),
        ):
            result = _bc1_auto_assign_pickup_and_laundry(order)

        self.assertTrue(result["laundry_assigned"])
        self.assertTrue(result["driver_assigned"])
