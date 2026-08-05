"""
Audit de stabilite (lot 1) - api_wallet_solde (orders/ops_api.py) etait
consultable sans aucune verification d'identite : n'importe qui connaissant
un partner_id/partner_type pouvait recuperer le solde et les 20 dernieres
transactions de n'importe quel pressing ou livreur. Corrige en exigeant le
JWT partenaire/livreur (pid/did) correspondant, sans exiger de jeton OPS.
"""
import jwt
from django.conf import settings
from django.test import TestCase

from partners.models import LaundryPartner, DeliveryPartner
from wallets.models import Wallet


def _token_partner(partner_id):
    return jwt.encode({'pid': partner_id, 'name': 'Test'}, settings.SECRET_KEY, algorithm='HS256')


def _token_driver(driver_id):
    return jwt.encode({'did': driver_id, 'name': 'Test'}, settings.SECRET_KEY, algorithm='HS256')


def _headers(token):
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


class ApiWalletSoldeAuthTests(TestCase):
    def setUp(self):
        self.pressing = LaundryPartner.objects.create(name="Pressing A", phone="0700000900", is_active=True)
        self.autre_pressing = LaundryPartner.objects.create(name="Pressing B", phone="0700000901", is_active=True)
        self.livreur = DeliveryPartner.objects.create(name="Livreur A", phone="0700000902", is_active=True)
        Wallet.objects.create(laundry_partner=self.pressing, currency='XOF', balance=15000)

    def test_sans_jeton_refuse(self):
        """Preuve du correctif : avant, aucune authentification n'etait requise ici."""
        response = self.client.post(
            "/api/wallet/solde/",
            data={"partner_type": "pressing", "partner_id": self.pressing.id},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_jeton_du_bon_pressing_accepte(self):
        response = self.client.post(
            "/api/wallet/solde/",
            data={"partner_type": "pressing", "partner_id": self.pressing.id},
            content_type="application/json",
            **_headers(_token_partner(self.pressing.id)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["solde"], 15000.0)

    def test_jeton_dun_autre_pressing_refuse(self):
        """Le coeur du bug : consulter le solde d'un AUTRE pressing que le sien."""
        response = self.client.post(
            "/api/wallet/solde/",
            data={"partner_type": "pressing", "partner_id": self.pressing.id},
            content_type="application/json",
            **_headers(_token_partner(self.autre_pressing.id)),
        )
        self.assertEqual(response.status_code, 403)

    def test_jeton_livreur_ne_peut_pas_lire_un_solde_pressing(self):
        response = self.client.post(
            "/api/wallet/solde/",
            data={"partner_type": "pressing", "partner_id": self.pressing.id},
            content_type="application/json",
            **_headers(_token_driver(self.livreur.id)),
        )
        self.assertEqual(response.status_code, 403)

    def test_jeton_du_bon_livreur_accepte(self):
        response = self.client.post(
            "/api/wallet/solde/",
            data={"partner_type": "livreur", "partner_id": self.livreur.id},
            content_type="application/json",
            **_headers(_token_driver(self.livreur.id)),
        )
        self.assertEqual(response.status_code, 200)
