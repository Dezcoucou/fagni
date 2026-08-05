"""
Tests de la conversion essai -> abonnement - FAGNI V1 (Lot 5, 28 juillet 2026).
"""
from decimal import Decimal
from django.test import TestCase

from orders.models import Customer, Order, Abonnement, AbonnementPricingRule, EvenementRoutine


class ApiRoutineEssaiDetailTests(TestCase):
    def setUp(self):
        AbonnementPricingRule.objects.create(
            pack="confort", taille_sac="M", prix_hebdomadaire=14800, is_active=True,
        )
        self.customer = Customer.objects.create(name="Client Test", phone="0700000700", address="Riviera 3")

    def test_essai_satisfaction_positive_accessible(self):
        order = Order.objects.create(
            customer=self.customer, order_origin="routine_trial", satisfaction_reponse="positive",
            routine_choisie="duo", pricing_mode="bag", bag_size="M",
        )
        response = self.client.get(f"/api/routine/essai/{order.code}/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["routine"], "duo")
        self.assertEqual(data["prix"], 59200.0)  # 14800 x 4 (facturation mensuelle)
        self.assertEqual(data["telephone"], "0700000700")

    def test_essai_satisfaction_pending_bloque(self):
        order = Order.objects.create(
            customer=self.customer, order_origin="routine_trial", satisfaction_reponse="pending",
            pricing_mode="bag", bag_size="M",
        )
        response = self.client.get(f"/api/routine/essai/{order.code}/")
        self.assertEqual(response.status_code, 422)

    def test_essai_introuvable_404(self):
        response = self.client.get("/api/routine/essai/ODRINTROUVABLE/")
        self.assertEqual(response.status_code, 404)

    def test_essai_non_enumerable_par_id_sequentiel(self):
        """Preuve du correctif : l'id sequentiel de la commande ne doit plus jamais donner acces a l'essai."""
        order = Order.objects.create(
            customer=self.customer, order_origin="routine_trial", satisfaction_reponse="positive",
            routine_choisie="duo", pricing_mode="bag", bag_size="M",
        )
        response = self.client.get(f"/api/routine/essai/{order.id}/")
        self.assertEqual(response.status_code, 404)


class ApiAbonnementReserverAvecEssaiOrigineTests(TestCase):
    def setUp(self):
        AbonnementPricingRule.objects.create(
            pack="confort", taille_sac="M", prix_hebdomadaire=14800, is_active=True,
        )
        self.customer = Customer.objects.create(name="Client Test", phone="0700000701", address="Riviera 3")
        self.order = Order.objects.create(
            customer=self.customer, order_origin="routine_trial", satisfaction_reponse="positive",
            routine_choisie="duo", pricing_mode="bag", bag_size="M",
        )

    def _payload(self, **overrides):
        base = {
            "telephone": "0700000701", "nom": "Client Test",
            "pack": "confort", "taille_sac": "M", "jour_collecte": 0, "jour_livraison": 3,
            "essai_origine": self.order.code,
        }
        base.update(overrides)
        return base

    def test_conversion_pose_essai_origine(self):
        response = self.client.post(
            "/api/abonnement/reserver/", data=self._payload(), content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        abonnement = Abonnement.objects.get(customer=self.customer)
        self.assertEqual(abonnement.essai_origine_id, self.order.id)

    def test_conversion_cree_evenement_abonnement_active(self):
        self.client.post("/api/abonnement/reserver/", data=self._payload(), content_type="application/json")
        self.assertTrue(EvenementRoutine.objects.filter(type_evenement="abonnement_active").exists())

    def test_essai_deja_converti_refuse_deuxieme_abonnement(self):
        self.client.post("/api/abonnement/reserver/", data=self._payload(), content_type="application/json")

        # Deuxieme tentative avec un AUTRE client sur le meme essai (cas limite)
        autre_client = Customer.objects.create(name="Autre", phone="0700000702", address="Riviera 3")
        response = self.client.post(
            "/api/abonnement/reserver/",
            data=self._payload(telephone="0700000702", nom="Autre"),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Abonnement.objects.filter(essai_origine=self.order).count(), 1)

    def test_essai_origine_inexistant_404(self):
        response = self.client.post(
            "/api/abonnement/reserver/", data=self._payload(essai_origine="ODRINTROUVABLE"),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_reservation_sans_essai_origine_fonctionne_toujours(self):
        """Non-regression explicite : le Lot 3 (24 juillet) doit continuer de fonctionner sans essai_origine."""
        payload = self._payload()
        del payload["essai_origine"]
        payload["telephone"] = "0700000703"

        response = self.client.post(
            "/api/abonnement/reserver/", data=payload, content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        abonnement = Abonnement.objects.get(customer__phone="0700000703")
        self.assertIsNone(abonnement.essai_origine)
