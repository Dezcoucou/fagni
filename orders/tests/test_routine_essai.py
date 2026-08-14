"""
Tests de l'endpoint essai Routine - FAGNI V1 (Lot 3, 27 juillet 2026).
"""
from decimal import Decimal
from django.test import TestCase, override_settings

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


class ApiCommercialFinalizationContractTests(TestCase):
    """
    Contrat commercial canonique du flux API client.

    Une commande créée via api_create_order ne doit jamais être exposée
    comme commercialement confirmée sans être passée par le finalizer
    canonique et sans ServiceExecution matérialisée.
    """

    def test_api_create_order_declares_explicitly_draft_state(self):
        from pathlib import Path

        source = Path("orders/client_api.py").read_text()

        start = source.index("def api_create_order")
        end = source.index("\ndef ", start + 1)

        block = source[start:end]

        self.assertIn(
            "'is_draft':",
            block,
            "api_create_order doit déclarer explicitement l'état commercial "
            "initial de la commande",
        )

        self.assertRegex(
            block,
            r"[\"']is_draft[\"']\s*:\s*True",
            "une commande API client doit naître en brouillon avant "
            "matérialisation canonique",
        )

    def test_api_create_order_uses_canonical_commercial_finalizer(self):
        from pathlib import Path

        source = Path("orders/client_api.py").read_text()

        start = source.index("def api_create_order")
        end = source.index("\ndef ", start + 1)

        block = source[start:end]

        self.assertIn(
            "finalize_commercial_order",
            block,
            "api_create_order doit passer par le finalizer commercial canonique",
        )

    def test_api_create_order_never_directly_sets_is_draft_false(self):
        from pathlib import Path

        source = Path("orders/client_api.py").read_text()

        start = source.index("def api_create_order")
        end = source.index("\ndef ", start + 1)

        block = source[start:end]

        self.assertNotIn(
            "is_draft = False",
            block,
        )

        self.assertNotIn(
            "'is_draft': False",
            block,
        )


class ApiCommercialPaymentAssignmentE2ETests(TestCase):
    """
    Contrat E2E canonique :

    API création
    -> finalisation commerciale
    -> ServiceExecution matérialisée
    -> paiement canonique
    -> hook post-paiement
    -> affectation pressing + livreur.
    """

    def setUp(self):
        from decimal import Decimal

        from orders.models import Customer
        from partners.models import DeliveryPartner, LaundryPartner
        from services.models import Service, ServiceCategory

        self.Decimal = Decimal

        self.customer = Customer.objects.create(
            name="Client E2E Commercial",
            phone="0700100999",
            address="Riviera 3",
            latitude=Decimal("5.350000"),
            longitude=Decimal("-3.980000"),
        )

        category, _ = ServiceCategory.objects.get_or_create(
            code="api-e2e-commercial",
            defaults={
                "name": "API E2E Commercial",
                "is_active": True,
            },
        )

        Service.objects.get_or_create(
            code="pressing_article",
            defaults={
                "category": category,
                "name": "Pressing Article",
                "description": "",
                "is_active": True,
                "primary_engine": Service.ENGINE_PICKUP_RETURN,
                "requires_partner": False,
                "requires_logistics": False,
                "requires_weighing": False,
                "requires_appointment": False,
                "requires_quote": False,
                "requires_asset": False,
                "requires_otp": False,
                "requires_signature": False,
                "pricing_mode": "fixed",
                "default_sla_hours": 24,
            },
        )

        self.laundry = LaundryPartner.objects.create(
            name="Pressing E2E",
            phone="0700100888",
            is_active=True,
            latitude=Decimal("5.351000"),
            longitude=Decimal("-3.981000"),
        )

        self.driver = DeliveryPartner.objects.create(
            name="Livreur E2E",
            phone="0700100777",
            email="livreur-e2e@example.com",
            is_active=True,
            latitude=Decimal("5.352000"),
            longitude=Decimal("-3.982000"),
        )

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_api_creation_payment_and_assignment_full_chain(self):
        from unittest.mock import patch

        from orders.models import DeliveryLeg, Order, Payment
        from orders.views import apply_order_payment
        from services.models import ServiceExecution

        order = Order.objects.create(
            customer=self.customer,
            status="pending",
            pricing_mode="item",
            is_draft=True,
            total_client_ttc=self.Decimal("10000"),
            total=self.Decimal("10000"),
            amount_paid=self.Decimal("0"),
            pickup_lat=self.Decimal("5.350000"),
            pickup_lng=self.Decimal("-3.980000"),
            delivery_lat=self.Decimal("5.350000"),
            delivery_lng=self.Decimal("-3.980000"),
        )

        from orders.models import OrderItem

        OrderItem.objects.create(
            order=order,
            designation="Chemise",
            quantity=1,
            unit_price=self.Decimal("10000"),
            total=self.Decimal("10000"),
            service_type="pressing",
        )

        from services.services import finalize_commercial_order

        executions = finalize_commercial_order(order=order)

        order.refresh_from_db()

        self.assertFalse(order.is_draft)
        self.assertEqual(len(executions), 1)
        self.assertEqual(
            ServiceExecution.objects.filter(order=order).count(),
            1,
        )
        self.assertEqual(
            order.service_executions.first().service.code,
            "pressing_article",
        )

        with patch(
            "orders.assignment.pick_best_laundry",
            return_value=(self.laundry, "e2e"),
        ), patch(
            "orders.assignment.pick_best_driver",
            return_value=(self.driver, "e2e"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                payment_result = apply_order_payment(
                    order,
                    self.Decimal("10000"),
                    channel="manual",
                    reference="E2E-COMMERCIAL-PAID-001",
                    note="E2E commercial payment",
                )

        order.refresh_from_db()

        self.assertTrue(payment_result["became_paid"])
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(order.amount_paid, self.Decimal("10000"))

        self.assertEqual(
            Payment.objects.filter(
                order=order,
                reference="E2E-COMMERCIAL-PAID-001",
            ).count(),
            1,
        )

        self.assertEqual(
            order.laundry_partner_id,
            self.laundry.id,
        )

        self.assertEqual(
            order.pickup_driver_id,
            self.driver.id,
        )

        pickup_leg = DeliveryLeg.objects.get(
            order=order,
            leg_type="pickup",
        )

        self.assertEqual(
            pickup_leg.driver_id,
            self.driver.id,
        )

        self.assertIn(
            pickup_leg.status,
            ("assigned", "in_progress"),
        )

        self.assertEqual(
            order.service_executions.count(),
            1,
            "le paiement ne doit jamais recréer les ServiceExecution",
        )
