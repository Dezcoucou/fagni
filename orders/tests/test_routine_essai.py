"""
Tests de l'endpoint essai Routine - FAGNI V1 (Lot 3, 27 juillet 2026).
"""
from decimal import Decimal
from django.test import TestCase

from orders.models import Customer, Order, AbonnementPricingRule
from orders.presenters import build_order_finance_summary


class ApiRoutineEssaiTests(TestCase):
    def setUp(self):
        AbonnementPricingRule.objects.create(
            pack="confort", taille_sac="M", prix_hebdomadaire=14800, is_active=True,
        )

    def _payload(self, **overrides):
        base = {
            "telephone": "0700000400", "nom": "Test Essai Routine",
            "routine": "duo", "pack": "confort", "taille_sac": "M",
            "adresse": "Riviera 3",
        }
        base.update(overrides)
        return base

    def test_essai_reussi_cree_order_pas_abonnement(self):
        from orders.models import Abonnement

        response = self.client.post(
            "/api/routine/essai/", data=self._payload(), content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertFalse(data["already_exists"])
        self.assertEqual(data["prix"], 14800.0)

        order = Order.objects.get(id=data["order_id"])
        self.assertEqual(order.order_origin, "routine_trial")
        self.assertEqual(order.routine_proposee, "duo")
        self.assertEqual(order.total_client_ttc, Decimal("14800.00"))

        # Critere d'acceptation critique : jamais un Abonnement cree ici
        self.assertEqual(Abonnement.objects.count(), 0)

    def test_pricing_mode_reste_bag_jamais_routine_essai(self):
        """
        Critere d'acceptation (audit section 0) : pricing_mode doit
        toujours etre 'bag', jamais une nouvelle valeur qui casserait
        is_bag/is_item dans presenters.py.
        """
        response = self.client.post(
            "/api/routine/essai/", data=self._payload(), content_type="application/json",
        )
        order = Order.objects.get(id=response.json()["order_id"])
        self.assertEqual(order.pricing_mode, "bag")

        # Verification directe que le presenter fonctionne toujours normalement
        summary = build_order_finance_summary(order)
        self.assertIsNotNone(summary)

    def test_essai_deja_en_cours_retourne_le_meme(self):
        r1 = self.client.post("/api/routine/essai/", data=self._payload(), content_type="application/json")
        r2 = self.client.post("/api/routine/essai/", data=self._payload(), content_type="application/json")

        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["already_exists"])
        self.assertEqual(r1.json()["order_id"], r2.json()["order_id"])
        self.assertEqual(Order.objects.filter(order_origin="routine_trial").count(), 1)

    def test_nouvel_essai_possible_apres_livraison(self):
        """
        Le statut reel utilise par Order (voir Order.STATUS_CHOICES) est
        'done', jamais 'delivered' - une faute de frappe dans le filtre
        d'idempotence utilisait 'delivered'/'cancelled', ce qui bloquait
        en permanence tout nouvel essai pour un client ayant deja eu un
        essai termine (audit de stabilite, lot 2). Ce test utilise
        volontairement le vrai statut pour ne plus jamais reproduire ce
        piege.
        """
        r1 = self.client.post("/api/routine/essai/", data=self._payload(), content_type="application/json")
        order1 = Order.objects.get(id=r1.json()["order_id"])
        order1.status = "done"
        order1.save()

        r2 = self.client.post("/api/routine/essai/", data=self._payload(), content_type="application/json")
        self.assertFalse(r2.json()["already_exists"])
        self.assertNotEqual(r1.json()["order_id"], r2.json()["order_id"])

    def test_nouvel_essai_possible_apres_annulation(self):
        """Meme correctif : un essai 'canceled' (jamais 'cancelled') ne doit plus bloquer un nouvel essai."""
        r1 = self.client.post("/api/routine/essai/", data=self._payload(), content_type="application/json")
        order1 = Order.objects.get(id=r1.json()["order_id"])
        order1.status = "canceled"
        order1.save()

        r2 = self.client.post("/api/routine/essai/", data=self._payload(), content_type="application/json")
        self.assertFalse(r2.json()["already_exists"])
        self.assertNotEqual(r1.json()["order_id"], r2.json()["order_id"])

    def test_routine_invalide_refusee(self):
        response = self.client.post(
            "/api/routine/essai/", data=self._payload(routine="inexistante"),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_offre_non_active_refusee(self):
        response = self.client.post(
            "/api/routine/essai/", data=self._payload(pack="essentiel"),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422)

    def test_api_create_order_totalement_inchange(self):
        """
        Non-regression explicite (spec section 12, Lot 6) - verifie ici en
        avance. Lit le fichier source directement (pas inspect.getsource
        sur la fonction decoree @api_view, qui ne renvoie que le wrapper
        DRF generique, pas le vrai corps de la fonction).
        """
        with open('orders/client_api.py') as f:
            source = f.read()
        idx_debut = source.index("def api_create_order")
        idx_fin = source.index("def api_articles")  # fonction suivante connue
        bloc_api_create_order = source[idx_debut:idx_fin]

        self.assertIn("Pricing v3.0", bloc_api_create_order)
        self.assertIn("GlobalPricingSettings", bloc_api_create_order)
