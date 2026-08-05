"""
Audit de stabilite - point d'arret "anti-double-paiement OPS" :
api_ops_enregistrer_paiement (branche paiement pressing/livreur, hors
retrait wallet) n'avait aucun garde-fou contre un double enregistrement -
confirme cote fagni-ops : le bouton "Confirmer paiement" n'est pas
desactive apres le premier clic et aucune cle d'idempotence n'est envoyee.
"""
import jwt
from django.conf import settings
from django.test import TestCase

from orders.models import Paiement


def _headers_ops():
    token = jwt.encode({'ops': True, 'name': 'Test OPS'}, settings.SECRET_KEY, algorithm='HS256')
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


class AntiDoublePaiementOpsTests(TestCase):
    def _payer(self, montant=15000):
        return self.client.post(
            "/api/ops/paiements/enregistrer/",
            data={
                'partenaire_type': 'livreur',
                'partenaire_id': 42,
                'partenaire_nom': 'Livreur Test',
                'montant': montant,
                'nb_commandes': 5,
                'wave_number': '0700009900',
                'note': 'Reglement hebdo',
            },
            **_headers_ops(),
        )

    def test_premier_paiement_est_enregistre(self):
        resp = self._payer()
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(Paiement.objects.count(), 1)

    def test_double_clic_immediat_est_refuse(self):
        r1 = self._payer()
        r2 = self._payer()

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 409)
        self.assertEqual(Paiement.objects.count(), 1)

    def test_paiement_dun_montant_different_nest_pas_bloque(self):
        self._payer(montant=15000)
        r2 = self._payer(montant=8000)

        self.assertEqual(r2.status_code, 200)
        self.assertEqual(Paiement.objects.count(), 2)

    def test_paiement_dun_autre_partenaire_nest_pas_bloque(self):
        self._payer()
        r2 = self.client.post(
            "/api/ops/paiements/enregistrer/",
            data={
                'partenaire_type': 'livreur',
                'partenaire_id': 99,
                'partenaire_nom': 'Autre Livreur',
                'montant': 15000,
                'nb_commandes': 5,
                'wave_number': '0700001111',
                'note': 'Reglement hebdo',
            },
            **_headers_ops(),
        )

        self.assertEqual(r2.status_code, 200)
        self.assertEqual(Paiement.objects.count(), 2)
