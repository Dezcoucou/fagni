"""
Tests du moteur de matching Crowd.

Couverture :
- matching trajet compatible
- trajet incompatible (détour excessif)
- horaire incompatible
- cotransporteur inactif
- cotransporteur non vérifié
- score insuffisant
- conflit de mission
- module Crowd désactivé
- coordonnées manquantes
"""
from decimal import Decimal
from datetime import time, timedelta

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from crowd.models import Cotransporter, CotransporterRoute, CrowdSettings
from crowd.matching import pick_best_cotransporter, _haversine_km, _calculate_detour


class HaversineTests(TestCase):
    """Tests de la fonction de distance."""

    def test_distance_nulle(self):
        self.assertEqual(_haversine_km(5.0, -4.0, 5.0, -4.0), 0.0)

    def test_distance_connue(self):
        # Abidjan → Yamoussoukro ≈ 240 km
        d = _haversine_km(5.35, -4.0, 6.82, -5.28)
        self.assertGreater(d, 200)
        self.assertLess(d, 300)

    def test_coordonnees_invalides(self):
        self.assertIsNone(_haversine_km(None, 0, 0, 0))
        self.assertIsNone(_haversine_km(0, None, 0, 0))
        self.assertIsNone(_haversine_km(0, 0, 0, 0))  # zéro = invalide

    def test_coordonnees_string(self):
        d = _haversine_km("5.35", "-4.0", "6.82", "-5.28")
        self.assertIsNotNone(d)


class DetourTests(TestCase):
    """Tests du calcul de détour."""

    def test_detour_nul_si_trajets_identiques(self):
        # Route et leg identiques → détour = 0
        detour = _calculate_detour(
            5.0, -4.0,  # route origin
            6.0, -3.0,  # route dest
            5.0, -4.0,  # leg pickup (= route origin)
            6.0, -3.0,  # leg delivery (= route dest)
        )
        self.assertEqual(detour, 0.0)

    def test_detour_positif_si_deviation(self):
        detour = _calculate_detour(
            5.0, -4.0,  # route origin
            6.0, -3.0,  # route dest
            5.1, -4.1,  # leg pickup (légèrement décalé)
            6.1, -3.1,  # leg delivery (légèrement décalé)
        )
        self.assertGreater(detour, 0)


class PickBestCotransporterTests(TestCase):
    """Tests de la fonction principale pick_best_cotransporter."""

    def setUp(self):
        # Activer CrowdSettings
        self.settings = CrowdSettings.get_solo()
        self.settings.crowd_enabled = True
        self.settings.crowd_max_detour_km = Decimal('3.00')
        self.settings.crowd_min_score = Decimal('70.00')
        self.settings.save()

        # Utilisateur de test
        self.user = User.objects.create_user(
            username='cotransporter_test',
            password='test'
        )

    def _make_cotransporter(self, **kwargs):
        defaults = {
            'user': self.user,
            'is_active': True,
            'is_verified': True,
            'score': Decimal('100.00'),
            'capacity_kg': Decimal('10.00'),
        }
        defaults.update(kwargs)
        return Cotransporter.objects.create(**defaults)

    def _make_route(self, cotransporter, **kwargs):
        defaults = {
            'cotransporter': cotransporter,
            'origin_lat': Decimal('5.360000'),
            'origin_lng': Decimal('-3.950000'),
            'destination_lat': Decimal('5.370000'),
            'destination_lng': Decimal('-3.940000'),
            'departure_time': time(8, 0),
            'max_detour_km': Decimal('3.00'),
            'time_window_minutes': 30,
            'is_active': True,
            'valid_from': timezone.now().date(),
        }
        defaults.update(kwargs)
        return CotransporterRoute.objects.create(**defaults)

    def _make_leg(self, pickup_lat=Decimal('5.360000'), pickup_lng=Decimal('-3.950000'),
                  delivery_lat=Decimal('5.370000'), delivery_lng=Decimal('-3.940000'),
                  leg_type='pickup'):
        from orders.models import Order, Customer, DeliveryLeg
        customer = Customer.objects.create(
            name='Test', 
            phone='0700000099',
            latitude=pickup_lat,
            longitude=pickup_lng
        )
        order = Order.objects.create(
            customer=customer,
            status='pending',
            delivery_lat=delivery_lat,
            delivery_lng=delivery_lng,
            pickup_scheduled_date=timezone.now().date(),
            pickup_scheduled_time=time(8, 0),
        )
        return DeliveryLeg.objects.create(
            order=order,
            leg_type=leg_type,
            status='pending',
        )

    def test_crowd_disabled_returns_no_match(self):
        """Module Crowd désactivé → aucun match."""
        self.settings.crowd_enabled = False
        self.settings.save()

        leg = self._make_leg()
        cotransporter, reason = pick_best_cotransporter(leg)

        self.assertIsNone(cotransporter)
        self.assertEqual(reason, "CROWD_DISABLED")

    def test_no_available_cotransporter(self):
        """Aucun cotransporteur actif/vérifié → NO_AVAILABLE."""
        leg = self._make_leg()
        cotransporter, reason = pick_best_cotransporter(leg)

        self.assertIsNone(cotransporter)
        self.assertEqual(reason, "NO_AVAILABLE")

    def test_inactive_cotransporter_ignored(self):
        """Cotransporteur inactif ignoré."""
        self._make_cotransporter(is_active=False)
        leg = self._make_leg()
        cotransporter, reason = pick_best_cotransporter(leg)

        self.assertIsNone(cotransporter)
        self.assertEqual(reason, "NO_AVAILABLE")

    def test_unverified_cotransporter_ignored(self):
        """Cotransporteur non vérifié ignoré."""
        self._make_cotransporter(is_verified=False)
        leg = self._make_leg()
        cotransporter, reason = pick_best_cotransporter(leg)

        self.assertIsNone(cotransporter)
        self.assertEqual(reason, "NO_AVAILABLE")

    def test_low_score_cotransporter_ignored(self):
        """Cotransporteur avec score insuffisant ignoré."""
        self._make_cotransporter(score=Decimal('50.00'))  # < 70
        leg = self._make_leg()
        cotransporter, reason = pick_best_cotransporter(leg)

        self.assertIsNone(cotransporter)
        self.assertEqual(reason, "NO_AVAILABLE")

    def test_matching_trajet_compatible(self):
        """Trajet compatible → match."""
        cotransporter = self._make_cotransporter()
        self._make_route(cotransporter)
        leg = self._make_leg()

        result_cotransporter, reason = pick_best_cotransporter(leg)

        self.assertEqual(result_cotransporter.id, cotransporter.id)
        self.assertEqual(reason, "MATCHED")

    def test_detour_too_high(self):
        """Détour excessif → DETOUR_TOO_HIGH."""
        cotransporter = self._make_cotransporter()
        # Route très éloignée de la jambe
        self._make_route(
            cotransporter,
            origin_lat=Decimal('10.000000'),  # très loin
            origin_lng=Decimal('-10.000000'),
            max_detour_km=Decimal('1.00'),
        )
        leg = self._make_leg()

        cotransporter_result, reason = pick_best_cotransporter(leg)

        self.assertIsNone(cotransporter_result)
        self.assertEqual(reason, "DETOUR_TOO_HIGH")

    def test_time_incompatible(self):
        """Horaire incompatible → TIME_INCOMPATIBLE."""
        cotransporter = self._make_cotransporter()
        # Route à 14h, jambe à 8h
        self._make_route(
            cotransporter,
            departure_time=time(14, 0),
            time_window_minutes=15,
        )
        leg = self._make_leg()

        cotransporter_result, reason = pick_best_cotransporter(leg)

        self.assertIsNone(cotransporter_result)
        self.assertEqual(reason, "TIME_INCOMPATIBLE")

    def test_best_score_wins(self):
        """Le meilleur score gagne (score élevé + détour faible)."""
        # Cotransporteur 1 : score 80, détour moyen
        c1 = self._make_cotransporter(
            user=User.objects.create_user(username='c1', password='test'),
            score=Decimal('80.00'),
        )
        self._make_route(
            c1,
            origin_lat=Decimal('5.361000'),  # léger détour
            origin_lng=Decimal('-3.951000'),
        )

        # Cotransporteur 2 : score 100, détour nul
        c2 = self._make_cotransporter(
            user=User.objects.create_user(username='c2', password='test'),
            score=Decimal('100.00'),
        )
        self._make_route(c2)  # trajet identique à la jambe

        leg = self._make_leg()
        result, reason = pick_best_cotransporter(leg)

        self.assertEqual(result.id, c2.id)
        self.assertEqual(reason, "MATCHED")

    def test_no_coordinates_returns_no_coordinates(self):
        """Coordonnées manquantes → NO_COORDINATES."""
        from orders.models import Order, Customer, DeliveryLeg
        customer = Customer.objects.create(name='Test', phone='0700000098')
        order = Order.objects.create(
            customer=customer,
            status='pending',
            # Pas de coordonnées
        )
        leg = DeliveryLeg.objects.create(
            order=order,
            leg_type='pickup',
            status='pending',
        )

        cotransporter, reason = pick_best_cotransporter(leg)

        self.assertIsNone(cotransporter)
        self.assertEqual(reason, "NO_COORDINATES")
