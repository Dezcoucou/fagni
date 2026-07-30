"""
Tests de l'ecran Accueil reel - FAGNI V1 (28 juillet 2026).
"""
import jwt
from django.conf import settings
from django.test import TestCase

from orders.models import Customer, Order


def _token(customer):
    return jwt.encode({'cid': customer.id, 'phone': customer.phone}, settings.SECRET_KEY, algorithm='HS256')


class ApiClientAccueilTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Awa Koné", phone="0700001000", address="Riviera 3")

    def test_sans_auth_refuse(self):
        response = self.client.get("/api/client/accueil/")
        self.assertEqual(response.status_code, 401)

    def test_avec_auth_sans_commande(self):
        response = self.client.get("/api/client/accueil/", HTTP_AUTHORIZATION=f"Bearer {_token(self.customer)}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["utilisateur"]["prenom"], "Awa")
        self.assertIsNone(data["commandeActive"])
        self.assertIsNone(data["fidelite"])
        self.assertEqual(len(data["services"]), 1)
        self.assertTrue(data["services"][0]["disponible"])

    def test_avec_commande_active_pending(self):
        Order.objects.create(customer=self.customer, status="pending", pricing_mode="bag", bag_size="M", routine_choisie="duo")
        response = self.client.get("/api/client/accueil/", HTTP_AUTHORIZATION=f"Bearer {_token(self.customer)}")
        data = response.json()
        self.assertIsNotNone(data["commandeActive"])
        self.assertEqual(data["commandeActive"]["service"], "Duo")
        self.assertEqual(data["commandeActive"]["etape"], 1)

    def test_commande_done_non_affichee_comme_active(self):
        Order.objects.create(customer=self.customer, status="done", pricing_mode="bag", bag_size="M")
        response = self.client.get("/api/client/accueil/", HTTP_AUTHORIZATION=f"Bearer {_token(self.customer)}")
        self.assertIsNone(response.json()["commandeActive"])

    def test_token_invalide_refuse(self):
        response = self.client.get("/api/client/accueil/", HTTP_AUTHORIZATION="Bearer invalidtoken")
        self.assertEqual(response.status_code, 401)
