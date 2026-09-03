"""
Tests de l'API Cotransporteur et de l'expiration.
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
from crowd.expiration import expire_stale_offers
from orders.models import Order, Customer, DeliveryLeg


def _make_token(cotransporter):
    payload = {
        'cotransporter_id': cotransporter.id,
        'exp': timezone.now() + timedelta(days=1),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')


class CrowdApiTests(TestCase):

    def setUp(self):
        self.settings = CrowdSettings.get_solo()
        self.settings.crowd_enabled = True
        self.settings.crowd_priority_over_pro = True
        self.settings.crowd_pickup_amount = Decimal('400')
        self.settings.crowd_max_detour_km = Decimal('3.00')
        self.settings.crowd_min_score = Decimal('70.00')
        self.settings.crowd_acceptance_timeout_seconds = 120
        self.settings.save()

        self.user = User.objects.create_user(username='test_crowd', password='test')
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
        self.token = _make_token(self.cotransporter)

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

    def test_list_offers_authenticated(self):
        """GET /api/crowd/offers/ avec token valide."""
        self._make_offer()
        
        response = self.client.get(
            '/api/crowd/offers/',
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['offers'][0]['leg_type'], 'pickup')

    def test_list_offers_unauthenticated(self):
        """GET /api/crowd/offers/ sans token → 401."""
        response = self.client.get('/api/crowd/offers/')
        self.assertEqual(response.status_code, 401)

    def test_accept_offer_success(self):
        """POST /api/crowd/offers/{id}/accept → 200."""
        leg = self._make_offer()
        
        response = self.client.post(
            f'/api/crowd/offers/{leg.id}/accept/',
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'accepted')
        
        leg.refresh_from_db()
        self.assertEqual(leg.status, 'assigned')
        self.assertEqual(leg.actor_type, 'cotransporter')

    def test_accept_offer_already_assigned(self):
        """POST /api/crowd/offers/{id}/accept sur offre déjà acceptée → 409."""
        leg = self._make_offer()
        
        # Première acceptation
        self.client.post(
            f'/api/crowd/offers/{leg.id}/accept/',
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
        )
        
        # Deuxième tentative
        response = self.client.post(
            f'/api/crowd/offers/{leg.id}/accept/',
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
        )
        
        self.assertEqual(response.status_code, 409)

    def test_reject_offer(self):
        """POST /api/crowd/offers/{id}/reject → 200."""
        leg = self._make_offer()
        
        response = self.client.post(
            f'/api/crowd/offers/{leg.id}/reject/',
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'rejected')

    def test_list_missions(self):
        """GET /api/crowd/missions/ après acceptation."""
        leg = self._make_offer()
        
        # Accepter l'offre
        self.client.post(
            f'/api/crowd/offers/{leg.id}/accept/',
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
        )
        
        response = self.client.get(
            '/api/crowd/missions/',
            HTTP_AUTHORIZATION=f'Bearer {self.token}',
        )
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['missions'][0]['status'], 'assigned')


class CrowdExpirationTests(TestCase):

    def setUp(self):
        self.settings = CrowdSettings.get_solo()
        self.settings.crowd_enabled = True
        self.settings.crowd_priority_over_pro = True
        self.settings.crowd_pickup_amount = Decimal('400')
        self.settings.crowd_max_detour_km = Decimal('3.00')
        self.settings.crowd_min_score = Decimal('70.00')
        self.settings.crowd_acceptance_timeout_seconds = 120
        self.settings.save()

        self.user = User.objects.create_user(username='test_expire', password='test')
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
        self.customer = Customer.objects.create(name='Client', phone='0700000098')

    def test_expire_stale_offers(self):
        """Les offres expirées sont rejetées automatiquement."""
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
        
        # Simuler expiration
        DeliveryLeg.objects.filter(pk=leg.pk).update(
            offer_expires_at=timezone.now() - timedelta(seconds=10)
        )
        
        result = expire_stale_offers()
        
        self.assertEqual(result['expired'], 1)
        self.assertEqual(result['errors'], 0)
        
        leg.refresh_from_db()
        self.assertEqual(leg.assignment_reason, 'EXPIRED')
        self.assertIsNone(leg.offered_at)

    def test_active_offers_not_expired(self):
        """Les offres actives ne sont pas touchées."""
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
        
        result = expire_stale_offers()
        
        self.assertEqual(result['expired'], 0)
        
        leg.refresh_from_db()
        self.assertIsNotNone(leg.offered_at)
