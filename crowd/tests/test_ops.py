"""
Tests de l'API OPS Crowd.
"""
import jwt
from decimal import Decimal
from datetime import time, timedelta

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from django.conf import settings

from crowd.models import Cotransporter, CotransporterRoute, CrowdSettings
from crowd.dispatch import dispatch_delivery_leg
from orders.models import Order, Customer, DeliveryLeg


def _make_ops_token():
    payload = {
        'ops': True,
        'name': 'Test OPS',
        'exp': timezone.now() + timedelta(days=1),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')


class OpsCrowdTests(TestCase):

    def setUp(self):
        self.settings = CrowdSettings.get_solo()
        self.settings.crowd_enabled = True
        self.settings.crowd_priority_over_pro = True
        self.settings.crowd_pickup_amount = Decimal('400')
        self.settings.crowd_max_detour_km = Decimal('3.00')
        self.settings.crowd_min_score = Decimal('70.00')
        self.settings.crowd_acceptance_timeout_seconds = 120
        self.settings.save()

        self.user = User.objects.create_user(username='test_ops_crowd', password='test')
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
        self.ops_token = _make_ops_token()

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

    def test_list_offers_unauthenticated(self):
        """GET /api/ops/crowd/offers/ sans token OPS → 401."""
        response = self.client.get('/api/ops/crowd/offers/')
        self.assertEqual(response.status_code, 401)

    def test_list_offers_authenticated(self):
        """GET /api/ops/crowd/offers/ avec token OPS → 200."""
        self._make_offer()
        
        response = self.client.get(
            '/api/ops/crowd/offers/',
            HTTP_AUTHORIZATION=f'Bearer {self.ops_token}',
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['status_filter'], 'pending')

    def test_list_offers_filter_expired(self):
        """GET /api/ops/crowd/offers/?status=expired."""
        leg = self._make_offer()
        
        # Simuler expiration
        DeliveryLeg.objects.filter(pk=leg.pk).update(
            offer_expires_at=timezone.now() - timedelta(seconds=10)
        )
        
        response = self.client.get(
            '/api/ops/crowd/offers/?status=expired',
            HTTP_AUTHORIZATION=f'Bearer {self.ops_token}',
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['status_filter'], 'expired')

    def test_stats_authenticated(self):
        """GET /api/ops/crowd/stats/ avec token OPS → 200."""
        self._make_offer()
        
        response = self.client.get(
            '/api/ops/crowd/stats/',
            HTTP_AUTHORIZATION=f'Bearer {self.ops_token}',
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('settings', data)
        self.assertIn('periods', data)
        self.assertIn('24h', data['periods'])
        self.assertEqual(data['periods']['24h']['total_offers'], 1)

    def test_force_pro_success(self):
        """POST /api/ops/crowd/offers/{id}/force-pro/ → fallback Pro."""
        leg = self._make_offer()
        
        response = self.client.post(
            f'/api/ops/crowd/offers/{leg.id}/force-pro/',
            HTTP_AUTHORIZATION=f'Bearer {self.ops_token}',
        )
        
        # Peut réussir ou échouer selon disponibilité Pro
        self.assertIn(response.status_code, [200, 400])
        
        leg.refresh_from_db()
        # L'offre Crowd doit être rejetée
        self.assertEqual(leg.assignment_reason, 'OPS_FORCE_PRO')

    def test_force_pro_not_crowd(self):
        """POST /api/ops/crowd/offers/{id}/force-pro/ sur leg non-Crowd → 400."""
        order = Order.objects.create(
            customer=self.customer,
            status='pending',
        )
        leg = DeliveryLeg.objects.create(
            order=order,
            leg_type='pickup',
            status='pending',
            assignment_source='professional',
        )
        
        response = self.client.post(
            f'/api/ops/crowd/offers/{leg.id}/force-pro/',
            HTTP_AUTHORIZATION=f'Bearer {self.ops_token}',
        )
        
        self.assertEqual(response.status_code, 400)
