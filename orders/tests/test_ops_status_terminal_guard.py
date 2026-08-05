"""
Audit de stabilite - point d'arret "matrice de transition de statut OPS" :
ops_update_status acceptait n'importe quelle valeur ALLOWED sans aucune
verification de transition - une commande 'done' (paiement deja declenche,
score partenaire deja recalcule) ou 'canceled' pouvait etre remise a
'pending'/'in_progress'/'ready' par une simple mise a jour de statut.

Correctif volontairement minimal et non ambigu : un statut terminal ne
regresse jamais. Les transitions entre statuts non-terminaux
(pending/in_progress/ready) restent libres - regle metier que l'audit
n'a pas ete en mesure de determiner avec certitude, donc non touchee ici.
"""
import jwt
from django.conf import settings
from django.test import TestCase

from orders.models import Customer, Order


def _headers_ops():
    token = jwt.encode({'ops': True, 'name': 'Test OPS'}, settings.SECRET_KEY, algorithm='HS256')
    return {'HTTP_AUTHORIZATION': f'Bearer {token}'}


class OpsStatusTerminalGuardTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Client Test", phone="0700009800", address="Riviera 3")

    def _order(self, status):
        return Order.objects.create(customer=self.customer, status=status)

    def _update(self, order, new_status):
        return self.client.post(
            f"/api/ops/orders/{order.id}/status/",
            data={'status': new_status},
            content_type='application/json',
            **_headers_ops(),
        )

    def test_refuse_de_faire_regresser_une_commande_terminee(self):
        order = self._order('done')
        resp = self._update(order, 'in_progress')

        self.assertEqual(resp.status_code, 409)
        order.refresh_from_db()
        self.assertEqual(order.status, 'done')

    def test_refuse_de_faire_regresser_une_commande_annulee(self):
        order = self._order('canceled')
        resp = self._update(order, 'pending')

        self.assertEqual(resp.status_code, 409)
        order.refresh_from_db()
        self.assertEqual(order.status, 'canceled')

    def test_refuse_meme_une_transition_terminal_vers_terminal(self):
        order = self._order('done')
        resp = self._update(order, 'canceled')

        self.assertEqual(resp.status_code, 409)
        order.refresh_from_db()
        self.assertEqual(order.status, 'done')

    def test_reappliquer_le_meme_statut_terminal_reste_accepte(self):
        order = self._order('done')
        resp = self._update(order, 'done')

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'done')

    def test_transitions_entre_statuts_non_terminaux_restent_libres(self):
        order = self._order('pending')
        resp = self._update(order, 'in_progress')
        self.assertEqual(resp.status_code, 200)

        resp2 = self._update(order, 'ready')
        self.assertEqual(resp2.status_code, 200)

        order.refresh_from_db()
        self.assertEqual(order.status, 'ready')

    def test_passage_vers_un_statut_terminal_reste_possible(self):
        order = self._order('in_progress')
        resp = self._update(order, 'done')

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, 'done')
