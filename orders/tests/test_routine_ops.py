"""
Tests du traitement OPS des essais Routine - FAGNI V1 (Lot 4, 28 juillet 2026).
"""
import jwt
from decimal import Decimal
from django.conf import settings
from django.test import TestCase

from orders.models import Customer, Order, AbonnementPricingRule, EvenementRoutine


def _token_ops():
    return jwt.encode({'ops': True, 'name': 'Test OPS'}, settings.SECRET_KEY, algorithm='HS256')


def _headers_ops():
    return {'HTTP_AUTHORIZATION': f'Bearer {_token_ops()}'}


class ApiOpsRoutineEssaisTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Client Test", phone="0700000600", address="Riviera 3")
        self.order = Order.objects.create(
            customer=self.customer, status="delivered", order_origin="routine_trial",
            routine_proposee="duo", routine_choisie="duo",
            pricing_mode="bag", bag_size="M", total_client_ttc=Decimal("14800"),
        )

    def test_liste_sans_auth_refusee(self):
        response = self.client.get("/api/ops/routine-essais/")
        self.assertEqual(response.status_code, 401)

    def test_liste_avec_auth_retourne_essai(self):
        response = self.client.get("/api/ops/routine-essais/", **_headers_ops())
        self.assertEqual(response.status_code, 200)
        essais = response.json()["essais"]
        self.assertEqual(len(essais), 1)
        self.assertEqual(essais[0]["id"], self.order.id)
        self.assertEqual(essais[0]["routine_proposee"], "duo")

    def test_filtre_par_satisfaction(self):
        self.order.satisfaction_reponse = "positive"
        self.order.save()
        Order.objects.create(
            customer=self.customer, status="delivered", order_origin="routine_trial",
            satisfaction_reponse="pending", pricing_mode="bag", bag_size="S",
        )

        response = self.client.get("/api/ops/routine-essais/?satisfaction=positive", **_headers_ops())
        essais = response.json()["essais"]
        self.assertEqual(len(essais), 1)
        self.assertEqual(essais[0]["satisfaction_reponse"], "positive")


class ApiOpsRoutineSatisfactionTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Client Test", phone="0700000601", address="Riviera 3")
        self.order = Order.objects.create(
            customer=self.customer, status="delivered", order_origin="routine_trial",
            pricing_mode="bag", bag_size="M",
        )

    def test_marquer_positive(self):
        response = self.client.post(
            f"/api/ops/routine-essais/{self.order.id}/satisfaction/",
            data={"reponse": "positive"}, content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()
        self.assertEqual(self.order.satisfaction_reponse, "positive")
        self.assertIsNotNone(self.order.satisfaction_contactee_le)

        self.assertTrue(EvenementRoutine.objects.filter(type_evenement="satisfaction_confirmee").exists())

    def test_reponse_invalide_refusee(self):
        response = self.client.post(
            f"/api/ops/routine-essais/{self.order.id}/satisfaction/",
            data={"reponse": "inexistante"}, content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(response.status_code, 400)

    def test_essai_introuvable_404(self):
        response = self.client.post(
            "/api/ops/routine-essais/99999/satisfaction/",
            data={"reponse": "positive"}, content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(response.status_code, 404)


class ApiOpsRoutineProposerAbonnementTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Client Test", phone="0700000602", address="Riviera 3")

    def test_proposition_bloquee_si_satisfaction_pending(self):
        order = Order.objects.create(
            customer=self.customer, status="delivered", order_origin="routine_trial",
            satisfaction_reponse="pending", pricing_mode="bag", bag_size="M",
        )
        response = self.client.post(
            f"/api/ops/routine-essais/{order.id}/proposer-abonnement/",
            content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(response.status_code, 422)

    def test_proposition_bloquee_si_incident_non_resolu(self):
        order = Order.objects.create(
            customer=self.customer, status="delivered", order_origin="routine_trial",
            satisfaction_reponse="incident", pricing_mode="bag", bag_size="M",
        )
        response = self.client.post(
            f"/api/ops/routine-essais/{order.id}/proposer-abonnement/",
            content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(response.status_code, 422)

    def test_proposition_autorisee_si_positive(self):
        order = Order.objects.create(
            customer=self.customer, status="delivered", order_origin="routine_trial",
            satisfaction_reponse="positive", routine_choisie="duo",
            pricing_mode="bag", bag_size="M",
        )
        response = self.client.post(
            f"/api/ops/routine-essais/{order.id}/proposer-abonnement/",
            content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(str(order.id), response.json()["lien_a_partager"])

        self.assertTrue(EvenementRoutine.objects.filter(type_evenement="abonnement_propose").exists())

    def test_proposition_autorisee_si_resolved(self):
        order = Order.objects.create(
            customer=self.customer, status="delivered", order_origin="routine_trial",
            satisfaction_reponse="resolved", pricing_mode="bag", bag_size="M",
        )
        response = self.client.post(
            f"/api/ops/routine-essais/{order.id}/proposer-abonnement/",
            content_type="application/json", **_headers_ops(),
        )
        self.assertEqual(response.status_code, 200)
