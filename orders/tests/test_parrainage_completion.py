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


class CompletionParrainageLivreurPressingTests(TestCase):
    def setUp(self):
        from partners.models import DeliveryPartner, LaundryPartner

        self.parrain_livreur = DeliveryPartner.objects.create(name="Parrain Livreur", phone="0700000093", vehicle_type="moto")
        self.filleul_livreur = DeliveryPartner.objects.create(name="Filleul Livreur", phone="0700000094", vehicle_type="moto")

        self.parrain_pressing = LaundryPartner.objects.create(name="Parrain Pressing", phone="0700000095", partner_type="standard")
        self.filleul_pressing = LaundryPartner.objects.create(name="Filleul Pressing", phone="0700000096", partner_type="standard")

        self.parrainage_livreur = Parrainage.objects.create(
            parrain_type="livreur", parrain_id=self.parrain_livreur.id, parrain_nom=self.parrain_livreur.name,
            filleul_type="livreur", filleul_id=self.filleul_livreur.id, filleul_nom=self.filleul_livreur.name,
            code_parrainage="LIVCODE01",
            statut="inscrit", actions_requises=10, nb_actions=0,
            remuneration_parrain=2000, remuneration_filleul=0,
        )

        self.parrainage_pressing = Parrainage.objects.create(
            parrain_type="pressing", parrain_id=self.parrain_pressing.id, parrain_nom=self.parrain_pressing.name,
            filleul_type="pressing", filleul_id=self.filleul_pressing.id, filleul_nom=self.filleul_pressing.name,
            code_parrainage="PRECODE01",
            statut="inscrit", actions_requises=10, nb_actions=0,
            remuneration_parrain=5000, remuneration_filleul=0,
        )

        self.client_customer = Customer.objects.create(name="Client Test", phone="0700000097", address="Test")

    def _payer_commande(self, order):
        with patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None):
            from orders.presenters import build_order_finance_summary
            total = Decimal(str(build_order_finance_summary(order)["total_client_ttc"]))
            apply_order_payment(order, total, channel="manual")

    def test_seuil_10_actions_requises_avant_completion(self):
        wallet_parrain = get_or_create_wallet_for_delivery_partner_helper(self.parrain_livreur)
        solde_avant = wallet_parrain.balance

        for i in range(9):
            order = Order.objects.create(
                customer=self.client_customer, status="pending", payment_status="unpaid",
                pricing_mode="bag", bag_size="medium", amount_paid=Decimal("0"),
                pickup_driver=self.filleul_livreur,
            )
            order.update_financials(save=True)
            self._payer_commande(order)

        self.parrainage_livreur.refresh_from_db()
        self.assertEqual(self.parrainage_livreur.nb_actions, 9)
        self.assertEqual(self.parrainage_livreur.statut, "inscrit")

        wallet_parrain.refresh_from_db()
        self.assertEqual(wallet_parrain.balance, solde_avant)

    def test_10eme_action_declenche_la_recompense_livreur(self):
        for i in range(10):
            order = Order.objects.create(
                customer=self.client_customer, status="pending", payment_status="unpaid",
                pricing_mode="bag", bag_size="medium", amount_paid=Decimal("0"),
                pickup_driver=self.filleul_livreur,
            )
            order.update_financials(save=True)
            self._payer_commande(order)

        self.parrainage_livreur.refresh_from_db()
        self.assertEqual(self.parrainage_livreur.statut, "actif")
        self.assertTrue(self.parrainage_livreur.cash_active)

        wallet_parrain = get_or_create_wallet_for_delivery_partner_helper(self.parrain_livreur)
        self.assertEqual(wallet_parrain.balance, Decimal("2000"))

    def test_10eme_action_declenche_la_recompense_pressing(self):
        for i in range(10):
            order = Order.objects.create(
                customer=self.client_customer, status="pending", payment_status="unpaid",
                pricing_mode="bag", bag_size="medium", amount_paid=Decimal("0"),
                laundry_partner=self.filleul_pressing,
            )
            order.update_financials(save=True)
            self._payer_commande(order)

        self.parrainage_pressing.refresh_from_db()
        self.assertEqual(self.parrainage_pressing.statut, "actif")

        wallet_parrain = get_or_create_wallet_for_laundry_partner_helper(self.parrain_pressing)
        self.assertEqual(wallet_parrain.balance, Decimal("5000"))

    def test_deux_livreurs_distincts_comptent_separement(self):
        """Collecte et livraison peuvent etre 2 livreurs differents - chacun compte independamment."""
        parrain_livraison = None
        from partners.models import DeliveryPartner
        parrain_livraison_partner = DeliveryPartner.objects.create(name="Parrain Livraison", phone="0700000098", vehicle_type="moto")
        filleul_livraison = DeliveryPartner.objects.create(name="Filleul Livraison", phone="0700000099", vehicle_type="moto")

        parrainage_livraison = Parrainage.objects.create(
            parrain_type="livreur", parrain_id=parrain_livraison_partner.id, parrain_nom=parrain_livraison_partner.name,
            filleul_type="livreur", filleul_id=filleul_livraison.id, filleul_nom=filleul_livraison.name,
            code_parrainage="LIVCODE02",
            statut="inscrit", actions_requises=10, nb_actions=0,
            remuneration_parrain=2000, remuneration_filleul=0,
        )

        order = Order.objects.create(
            customer=self.client_customer, status="pending", payment_status="unpaid",
            pricing_mode="bag", bag_size="medium", amount_paid=Decimal("0"),
            pickup_driver=self.filleul_livreur, delivery_partner=filleul_livraison,
        )
        order.update_financials(save=True)
        self._payer_commande(order)

        self.parrainage_livreur.refresh_from_db()
        parrainage_livraison.refresh_from_db()
        self.assertEqual(self.parrainage_livreur.nb_actions, 1)
        self.assertEqual(parrainage_livraison.nb_actions, 1)


def get_or_create_wallet_for_delivery_partner_helper(partner):
    from wallets.services import get_or_create_wallet_for_delivery_partner
    return get_or_create_wallet_for_delivery_partner(partner)


def get_or_create_wallet_for_laundry_partner_helper(partner):
    from wallets.services import get_or_create_wallet_for_laundry_partner
    return get_or_create_wallet_for_laundry_partner(partner)
