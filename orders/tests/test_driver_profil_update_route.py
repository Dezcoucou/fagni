"""
Audit de stabilite (lot 5) - api_driver_profil_update (orders/driver_api.py)
existait et fonctionnait, mais n'avait jamais ete montee sur une route :
chaque tentative de mise a jour du numero Wave depuis l'app livreur
echouait en 404, remontee cote frontend comme un message generique
"Erreur lors de la sauvegarde" qui ne disait jamais que la fonctionnalite
etait structurellement cassee.
"""
import json

import jwt
from django.conf import settings
from django.test import TestCase

from partners.models import DeliveryPartner


def _headers(driver_id):
    token = jwt.encode({'did': driver_id, 'name': 'Test'}, settings.SECRET_KEY, algorithm='HS256')
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


class DriverProfilUpdateRouteTests(TestCase):
    def test_route_existe_et_met_a_jour_le_numero_wave(self):
        driver = DeliveryPartner.objects.create(name="Livreur Test", phone="0700009001", is_active=True)

        resp = self.client.post(
            "/api/driver/profil/update/",
            data=json.dumps({"wave_number": "0700009001"}),
            content_type="application/json",
            **_headers(driver.id),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["wave_number"], "0700009001")

        driver.refresh_from_db()
        self.assertEqual(driver.wave_number, "0700009001")
