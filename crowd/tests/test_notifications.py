"""
Tests des notifications FCM Crowd.
"""
from decimal import Decimal
from datetime import time
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from crowd.models import Cotransporter, CotransporterRoute, CrowdSettings
from crowd.dispatch import dispatch_delivery_leg
from crowd.notifications import (
    notify_cotransporter_of_offer,
    notify_cotransporter_offer_accepted,
    notify_cotransporter_offer_expired,
)
from orders.models import Order, Customer, DeliveryLeg, FCMToken


class CrowdNotificationTests(TestCase):

    def setUp(self):
        self.settings = CrowdSettings.get_solo()
        self.settings.crowd_enabled = True
        self.settings.crowd_priority_over_pro = True
        self.settings.crowd_pickup_amount = Decimal('400')
        self.settings.crowd_max_detour_km = Decimal('3.00')
        self.settings.crowd_min_score = Decimal('70.00')
        self.settings.crowd_acceptance_timeout_seconds = 120
        self.settings.save()

        self.user = User.objects.create_user(username='test_notif', password='test')
        self.cotransporter = Cotransporter.objects.create(
            user=self.user,
            is_active=True,
            is_verified=True,
            score=Decimal('100'),
            capacity_kg=Decimal('10'),
        )
        CotransporterRoute.objects.create(
            cotransporter=self.cotransporter,
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
        self.customer = Customer.objects.create(name='Client', phone='0700000099')

    def _make_offer(self):
        order = Order.objects.create(
            customer=self.customer,
            status='pending',
            pickup_lat=5.36,
            pickup_lng=-3.95,
            delivery_lat=5.37,
            delivery_lng=-3.94,
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )
        leg = DeliveryLeg.objects.create(order=order, leg_type='pickup', status='pending')
        dispatch_delivery_leg(leg)
        leg.refresh_from_db()
        return leg

    @patch("crowd.notifications.send_push")
    def test_notify_offer_with_token(self, mock_send):
        """Notification envoyée si token FCM existe."""
        mock_send.return_value = True
        
        FCMToken.objects.create(
            user_type='cotransporter',
            user_id=self.cotransporter.id,
            token='test_token_123',
        )
        
        leg = self._make_offer()
        result = notify_cotransporter_of_offer(leg, self.cotransporter)
        
        self.assertTrue(result)
        mock_send.assert_called()
        
        call_args = mock_send.call_args
        self.assertEqual(call_args.kwargs['token'], 'test_token_123')
        self.assertIn("mission", call_args.kwargs['title'].lower())

    @patch("crowd.notifications.send_push")
    def test_notify_offer_without_token(self, mock_send):
        """Pas de notification si pas de token."""
        leg = self._make_offer()
        result = notify_cotransporter_of_offer(leg, self.cotransporter)
        
        self.assertFalse(result)
        mock_send.assert_not_called()

    @patch("crowd.notifications.send_push")
    def test_notify_accepted(self, mock_send):
        """Notification d'acceptation."""
        mock_send.return_value = True
        
        FCMToken.objects.create(
            user_type='cotransporter',
            user_id=self.cotransporter.id,
            token='test_token_456',
        )
        
        leg = self._make_offer()
        result = notify_cotransporter_offer_accepted(leg, self.cotransporter)
        
        self.assertTrue(result)
        mock_send.assert_called()

    @patch("crowd.notifications.send_push")
    def test_notify_expired(self, mock_send):
        """Notification d'expiration."""
        mock_send.return_value = True
        
        FCMToken.objects.create(
            user_type='cotransporter',
            user_id=self.cotransporter.id,
            token='test_token_789',
        )
        
        leg = self._make_offer()
        result = notify_cotransporter_offer_expired(leg, self.cotransporter)
        
        self.assertTrue(result)
        mock_send.assert_called()

    @patch("crowd.notifications.send_push")
    def test_dispatch_triggers_notification(self, mock_send):
        """dispatch_delivery_leg() déclenche automatiquement la notification."""
        mock_send.return_value = True
        
        FCMToken.objects.create(
            user_type='cotransporter',
            user_id=self.cotransporter.id,
            token='test_token_auto',
        )
        
        order = Order.objects.create(
            customer=self.customer,
            status='pending',
            pickup_lat=5.36,
            pickup_lng=-3.95,
            delivery_lat=5.37,
            delivery_lng=-3.94,
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )
        leg = DeliveryLeg.objects.create(order=order, leg_type='pickup', status='pending')
        
        actor, reason, mode = dispatch_delivery_leg(leg)
        
        self.assertEqual(mode, "offered")
        mock_send.assert_called()
