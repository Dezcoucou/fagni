"""
Contrat P0 DeliveryLeg Crowd — règle métier Phase 14.

Une offre Crowd reste pending. ``assigned`` est produit exclusivement par
l'acceptation du cotransporteur et un return ne devient acceptable qu'après
pickup done et linge prêt.
"""

from datetime import time
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TransactionTestCase
from django.utils import timezone

from crowd.dispatch import accept_crowd_offer, dispatch_delivery_leg
from crowd.models import Cotransporter, CotransporterRoute, CrowdSettings
from orders.models import Customer, DeliveryLeg, Order
from partners.models import DeliveryPartner


class P0CrowdDeliveryLegBusinessContractTests(TransactionTestCase):
    def setUp(self):
        settings = CrowdSettings.get_solo()
        settings.crowd_enabled = True
        settings.crowd_priority_over_pro = True
        settings.crowd_pickup_amount = Decimal("400")
        settings.crowd_return_amount = Decimal("400")
        settings.crowd_max_detour_km = Decimal("3.00")
        settings.crowd_min_score = Decimal("70")
        settings.crowd_acceptance_timeout_seconds = 120
        settings.save()

        self.customer = Customer.objects.create(
            name="Client Crowd P0",
            phone="0700001410",
            address="Riviera 3",
        )
        self.pickup_driver = DeliveryPartner.objects.create(
            name="Driver pickup A Crowd",
            phone="0700001411",
            is_active=True,
            latitude=Decimal("5.360000"),
            longitude=Decimal("-3.950000"),
        )
        user = User.objects.create_user(username="crowd_p0_c", password="test")
        self.cotransporter = Cotransporter.objects.create(
            user=user,
            is_active=True,
            is_verified=True,
            score=Decimal("100"),
            capacity_kg=Decimal("10"),
        )
        CotransporterRoute.objects.create(
            cotransporter=self.cotransporter,
            # Une mission return est pressing -> client : la route Crowd
            # doit donc etre orientee delivery -> pickup.
            origin_lat=Decimal("5.370000"),
            origin_lng=Decimal("-3.940000"),
            destination_lat=Decimal("5.360000"),
            destination_lng=Decimal("-3.950000"),
            departure_time=time(8, 0),
            max_detour_km=Decimal("3.00"),
            time_window_minutes=60,
            is_active=True,
            valid_from=timezone.now().date(),
        )

    def _make_professional_pickup_and_crowd_return(self, *, pickup_done=False, wash_ready=False):
        order = Order.objects.create(
            customer=self.customer,
            status="pending",
            payment_status="unpaid",
            total_client_ttc=5000,
            pickup_driver=self.pickup_driver,
            pickup_lat=5.36,
            pickup_lng=-3.95,
            delivery_lat=5.37,
            delivery_lng=-3.94,
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )
        pickup = DeliveryLeg.objects.create(
            order=order,
            leg_type="pickup",
            driver=self.pickup_driver,
            status="pending",
        )
        # Le return est cree avant wash_complete_time afin que le signal de
        # save pickup ne puisse pas en creer un second automatiquement.
        return_leg, _created = DeliveryLeg.objects.get_or_create(
            order=order,
            leg_type="return",
            defaults={"status": "pending"},
        )
        if wash_ready:
            order.wash_complete_time = timezone.now()
            order.save(update_fields=["wash_complete_time"])
        if pickup_done:
            pickup.status = "done"
            pickup.finished_at = timezone.now()
            pickup.save(update_fields=["status", "finished_at"])
        return order, pickup, return_leg

    def _offer_return_to_crowd(self, return_leg):
        actor, _reason, mode = dispatch_delivery_leg(return_leg)
        return_leg.refresh_from_db()
        self.assertEqual(mode, "offered")
        self.assertEqual(actor.id, self.cotransporter.id)
        return return_leg

    def test_p0_crowd_return_offer_stays_pending_before_cotransporter_accepts(self):
        _order, _pickup, return_leg = self._make_professional_pickup_and_crowd_return()
        self._offer_return_to_crowd(return_leg)

        self.assertEqual(return_leg.status, "pending")
        self.assertEqual(return_leg.assignment_source, "crowd")
        self.assertIsNotNone(return_leg.offered_at)
        self.assertIsNotNone(return_leg.offer_expires_at)
        self.assertIsNone(return_leg.cotransporter_id)
        self.assertIsNone(return_leg.driver_id)

    def test_p0_crowd_return_acceptance_is_refused_before_professional_pickup_a_done(self):
        _order, pickup, return_leg = self._make_professional_pickup_and_crowd_return(wash_ready=True)
        self._offer_return_to_crowd(return_leg)

        accepted, _reason = accept_crowd_offer(return_leg, self.cotransporter)
        pickup.refresh_from_db()
        return_leg.refresh_from_db()

        self.assertFalse(accepted)
        self.assertEqual(pickup.driver_id, self.pickup_driver.id)
        self.assertNotEqual(pickup.status, "done")
        self.assertEqual(return_leg.status, "pending")
        self.assertIsNone(return_leg.cotransporter_id)

    def test_p0_crowd_return_acceptance_is_refused_without_wash_complete_time(self):
        _order, pickup, return_leg = self._make_professional_pickup_and_crowd_return(pickup_done=True)
        self._offer_return_to_crowd(return_leg)

        accepted, _reason = accept_crowd_offer(return_leg, self.cotransporter)
        pickup.refresh_from_db()
        return_leg.refresh_from_db()

        self.assertFalse(accepted)
        self.assertEqual(pickup.status, "done")
        self.assertEqual(return_leg.status, "pending")
        self.assertIsNone(return_leg.cotransporter_id)

    def test_p0_crowd_return_acceptance_after_pickup_done_and_wash_ready_assigns_c_only(self):
        _order, pickup, return_leg = self._make_professional_pickup_and_crowd_return(
            pickup_done=True,
            wash_ready=True,
        )
        self._offer_return_to_crowd(return_leg)

        accepted, _reason = accept_crowd_offer(return_leg, self.cotransporter)
        pickup.refresh_from_db()
        return_leg.refresh_from_db()

        self.assertTrue(accepted)
        self.assertEqual(return_leg.status, "assigned")
        self.assertEqual(return_leg.actor_type, "cotransporter")
        self.assertEqual(return_leg.cotransporter_id, self.cotransporter.id)
        self.assertIsNotNone(return_leg.accepted_at)
        self.assertIsNone(return_leg.driver_id)
        self.assertEqual(pickup.driver_id, self.pickup_driver.id)
        self.assertEqual(pickup.status, "done")

    def test_p0_accepting_crowd_return_never_modifies_professional_pickup_a(self):
        _order, pickup, return_leg = self._make_professional_pickup_and_crowd_return(
            pickup_done=True,
            wash_ready=True,
        )
        self._offer_return_to_crowd(return_leg)

        accepted, _reason = accept_crowd_offer(return_leg, self.cotransporter)
        pickup.refresh_from_db()
        return_leg.refresh_from_db()

        self.assertTrue(accepted)
        self.assertEqual(pickup.driver_id, self.pickup_driver.id)
        self.assertEqual(pickup.status, "done")
        self.assertIsNone(pickup.cotransporter_id)
        self.assertEqual(return_leg.cotransporter_id, self.cotransporter.id)
        self.assertIsNone(return_leg.driver_id)
