"""
Tests de la completion de parrainage client - FAGNI V1 (24 juillet 2026).
Verifie que completer_parrainage_client_si_applicable() active bien la
recompense cash et credite le wallet du parrain, sans jamais faire
echouer le paiement de la commande elle-meme si un probleme survient.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from orders.models import Customer, Order, Parrainage
from orders.views import apply_order_payment
from wallets.services import get_or_create_wallet_for_customer


class CompletionParrainageClientTests(TestCase):
    def setUp(self):
        self.parrain = Customer.objects.create(name="Parrain Test", phone="0700000090", address="Test")
        self.filleul = Customer.objects.create(name="Filleul Test", phone="0700000091", address="Test")

        self.parrainage = Parrainage.objects.create(
            parrain_type="client", parrain_id=self.parrain.id, parrain_nom=self.parrain.name,
            filleul_type="client", filleul_id=self.filleul.id, filleul_nom=self.filleul.name,
            code_parrainage="TESTCODE1",
            statut="inscrit", actions_requises=1, nb_actions=0,
            remuneration_parrain=500, remuneration_filleul=500,
        )

        self.order = Order.objects.create(
            customer=self.filleul, status="pending", payment_status="unpaid",
            pricing_mode="bag", bag_size="medium", amount_paid=Decimal("0"),
        )
        self.order.update_financials(save=True)

    def test_premier_paiement_filleul_active_parrainage_et_credite_parrain(self):
        wallet_parrain = get_or_create_wallet_for_customer(self.parrain)
        solde_avant = wallet_parrain.balance

        with patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None):
            from orders.presenters import build_order_finance_summary
            total = Decimal(str(build_order_finance_summary(self.order)["total_client_ttc"]))
            apply_order_payment(self.order, total, channel="manual")

        self.parrainage.refresh_from_db()
        self.assertEqual(self.parrainage.statut, "actif")
        self.assertTrue(self.parrainage.cash_active)
        self.assertEqual(self.parrainage.nb_actions, 1)

        wallet_parrain.refresh_from_db()
        self.assertEqual(wallet_parrain.balance, solde_avant + Decimal("500"))

    def test_client_sans_parrainage_ne_leve_aucune_erreur(self):
        """Un client normal (sans filleul en attente) ne doit jamais faire planter le paiement."""
        autre_client = Customer.objects.create(name="Sans Parrainage", phone="0700000092", address="Test")
        order2 = Order.objects.create(
            customer=autre_client, status="pending", payment_status="unpaid",
            pricing_mode="bag", bag_size="medium", amount_paid=Decimal("0"),
        )
        order2.update_financials(save=True)

        with patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None):
            from orders.presenters import build_order_finance_summary
            total = Decimal(str(build_order_finance_summary(order2)["total_client_ttc"]))
            result = apply_order_payment(order2, total, channel="manual")

        self.assertTrue(result["became_paid"])

    def test_parrainage_deja_actif_non_recredite(self):
        """Un parrainage deja complete ne doit jamais etre recredite sur un deuxieme paiement."""
        self.parrainage.statut = "actif"
        self.parrainage.cash_active = True
        self.parrainage.nb_actions = 1
        self.parrainage.save()

        wallet_parrain = get_or_create_wallet_for_customer(self.parrain)
        solde_avant = wallet_parrain.balance

        with patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None):
            from orders.presenters import build_order_finance_summary
            total = Decimal(str(build_order_finance_summary(self.order)["total_client_ttc"]))
            apply_order_payment(self.order, total, channel="manual")

        wallet_parrain.refresh_from_db()
        self.assertEqual(wallet_parrain.balance, solde_avant)
