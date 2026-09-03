"""
Tests du dispatch hybride Crowd → Pro → OPS.
"""
from decimal import Decimal
from datetime import time, timedelta

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from unittest.mock import patch

from crowd.models import Cotransporter, CotransporterRoute, CrowdSettings
from crowd.dispatch import dispatch_delivery_leg, accept_crowd_offer, reject_crowd_offer
from orders.models import Order, Customer, DeliveryLeg
from partners.models import DeliveryPartner


class DispatchTests(TestCase):

    def setUp(self):
        self.settings = CrowdSettings.get_solo()
        self.settings.crowd_enabled = True
        self.settings.crowd_priority_over_pro = True
        self.settings.crowd_pickup_amount = Decimal('400')
        self.settings.crowd_return_amount = Decimal('400')
        self.settings.crowd_max_detour_km = Decimal('3.00')
        self.settings.crowd_min_score = Decimal('70.00')
        self.settings.crowd_acceptance_timeout_seconds = 120
        self.settings.save()

        self.customer = Customer.objects.create(
            name='Test Client',
            phone='0700000099',
        )

    def _make_order(self, pickup_lat=5.36, pickup_lng=-3.95):
        return Order.objects.create(
            customer=self.customer,
            status='pending',
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            delivery_lat=pickup_lat + 0.01,
            delivery_lng=pickup_lng + 0.01,
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )

    def _make_leg(self, order, leg_type='pickup'):
        return DeliveryLeg.objects.create(
            order=order,
            leg_type=leg_type,
            status='pending',
        )

    def _make_cotransporter(self, username, score=100):
        user = User.objects.create_user(username=username, password='test')
        return Cotransporter.objects.create(
            user=user,
            is_active=True,
            is_verified=True,
            score=Decimal(str(score)),
            capacity_kg=Decimal('10.00'),
        )

    def _make_route(self, cotransporter):
        return CotransporterRoute.objects.create(
            cotransporter=cotransporter,
            origin_lat=Decimal('5.360000'),
            origin_lng=Decimal('-3.950000'),
            destination_lat=Decimal('5.370000'),
            destination_lng=Decimal('-3.940000'),
            departure_time=time(8, 0),
            max_detour_km=Decimal('3.00'),
            time_window_minutes=60,
            is_active=True,
            valid_from=timezone.now().date(),
        )

    def _make_driver(self, name, phone):
        return DeliveryPartner.objects.create(
            name=name,
            phone=phone,
            is_active=True,
            latitude=Decimal('5.360000'),
            longitude=Decimal('-3.950000'),
        )

    def test_crowd_priority_when_enabled(self):
        """Crowd prioritaire si activé et match trouvé."""
        crowd = self._make_cotransporter('crowd1')
        self._make_route(crowd)
        order = self._make_order()
        leg = self._make_leg(order)

        actor, reason, mode = dispatch_delivery_leg(leg)

        self.assertIsNotNone(actor, f"reason={reason}, mode={mode}")
        self.assertEqual(actor.id, crowd.id)
        self.assertEqual(mode, "offered")
        self.assertEqual(leg.assignment_source, "crowd")

    @patch("crowd.dispatch.pick_best_driver")
    def test_fallback_pro_when_crowd_no_match(self, mock_pick):
        """Fallback Pro si Crowd ne trouve personne."""
        driver = self._make_driver('Driver Pro', '0700000100')
        mock_pick.return_value = (driver, "MATCHED")

        order = self._make_order()
        leg = self._make_leg(order)

        actor, reason, mode = dispatch_delivery_leg(leg)

        self.assertEqual(actor.id, driver.id)
        self.assertEqual(mode, "professional")
        self.assertEqual(leg.actor_type, "professional")
        self.assertEqual(leg.status, "assigned")

    @patch("crowd.dispatch.pick_best_driver", return_value=(None, "NO_MATCH"))
    def test_fallback_ops_when_no_match(self, mock_pick):
        """Fallback OPS si ni Crowd ni Pro ne trouvent."""
        order = self._make_order()
        leg = self._make_leg(order)

        actor, reason, mode = dispatch_delivery_leg(leg)

        self.assertIsNone(actor)
        self.assertEqual(mode, "ops")
        self.assertEqual(leg.assignment_source, "ops")

    @patch("crowd.dispatch.pick_best_driver")
    def test_crowd_disabled_skips_to_pro(self, mock_pick):
        """Crowd désactivé → directement Pro."""
        self.settings.crowd_enabled = False
        self.settings.save()

        crowd = self._make_cotransporter('crowd2')
        self._make_route(crowd)
        driver = self._make_driver('Driver Pro 2', '0700000101')
        mock_pick.return_value = (driver, "MATCHED")

        order = self._make_order()
        leg = self._make_leg(order)

        actor, reason, mode = dispatch_delivery_leg(leg)

        self.assertEqual(actor.id, driver.id)
        self.assertEqual(mode, "professional")

    def test_accept_crowd_offer_success(self):
        """Acceptation atomique d'une offre Crowd."""
        crowd = self._make_cotransporter('crowd3')
        self._make_route(crowd)
        order = self._make_order()
        leg = self._make_leg(order)

        actor, reason, mode = dispatch_delivery_leg(leg)
        self.assertEqual(mode, "offered")

        success, accept_reason = accept_crowd_offer(leg, crowd)
        self.assertTrue(success, f"reason={accept_reason}")

        leg.refresh_from_db()
        self.assertEqual(leg.actor_type, "cotransporter")
        self.assertEqual(leg.cotransporter_id, crowd.id)
        self.assertEqual(leg.status, "assigned")
        self.assertEqual(leg.driver_amount, Decimal('400'))

    def test_accept_crowd_offer_expired(self):
        """Offre expirée → rejetée."""
        crowd = self._make_cotransporter('crowd4')
        self._make_route(crowd)
        order = self._make_order()
        leg = self._make_leg(order)

        actor, reason, mode = dispatch_delivery_leg(leg)
        self.assertEqual(mode, "offered")

        # Simuler expiration
        DeliveryLeg.objects.filter(pk=leg.pk).update(
            offer_expires_at=timezone.now() - timedelta(seconds=10)
        )
        leg.refresh_from_db()

        success, reason = accept_crowd_offer(leg, crowd)
        self.assertFalse(success)
        self.assertEqual(reason, "OFFER_EXPIRED")

    def test_reject_crowd_offer(self):
        """Rejet d'une offre Crowd."""
        crowd = self._make_cotransporter('crowd5')
        self._make_route(crowd)
        order = self._make_order()
        leg = self._make_leg(order)

        actor, reason, mode = dispatch_delivery_leg(leg)
        self.assertEqual(mode, "offered")

        result = reject_crowd_offer(leg, reason="REJECTED")
        self.assertTrue(result)

        leg.refresh_from_db()
        self.assertIsNone(leg.offered_at)
        self.assertEqual(leg.assignment_reason, "REJECTED")

    def test_concurrent_acceptance_prevented(self):
        """Deux tentatives d'acceptation → une seule réussit."""
        crowd1 = self._make_cotransporter('crowd6')
        crowd2 = self._make_cotransporter('crowd7')
        self._make_route(crowd1)

        order = self._make_order()
        leg = self._make_leg(order)

        actor, reason, mode = dispatch_delivery_leg(leg)
        self.assertEqual(mode, "offered")

        success1, _ = accept_crowd_offer(leg, crowd1)
        self.assertTrue(success1)

        success2, reason = accept_crowd_offer(leg, crowd2)
        self.assertFalse(success2)
        self.assertEqual(reason, "ALREADY_ASSIGNED")

    def test_payout_works_for_crowd(self):
        """Payout fonctionne pour une jambe Crowd."""
        from orders.service_layer.payouts import trigger_driver_payout_for_leg

        crowd = self._make_cotransporter('crowd8')
        self._make_route(crowd)
        order = self._make_order()

        # Marquer comme payé via update() pour bypasser les guards
        Order.objects.filter(pk=order.pk).update(
            payment_status='paid',
            amount_paid=order.total_client_ttc
        )
        order.refresh_from_db()

        leg = self._make_leg(order)

        actor, reason, mode = dispatch_delivery_leg(leg)
        self.assertEqual(mode, "offered")

        success, accept_reason = accept_crowd_offer(leg, crowd)
        self.assertTrue(success)

        leg.status = 'done'
        leg.save()

        tx = trigger_driver_payout_for_leg(leg)
        self.assertIsNotNone(tx, "Payout devrait être créé")
        self.assertEqual(tx.amount, Decimal('400'))
        self.assertEqual(tx.wallet.owner_type, 'cotransporter')
        self.assertEqual(tx.wallet.cotransporter_id, crowd.id)
