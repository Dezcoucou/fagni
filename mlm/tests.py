from decimal import Decimal

from django.test import TestCase

from orders.models import (
    Customer,
    Order,
    ServiceCategory,
    ServiceItem,
    OrderItem,
)
from mlm.models import (
    ReferralLink,
    ReferralCommission,
    WalletTransaction,
)


class MLMBaseTestCase(TestCase):
    """
    Base commune : met en place une petite hiérarchie MLM sur 3 niveaux
    + utilitaires pour créer des commandes et recalculer les commissions.
    """

    def setUp(self):
        # Création des clients
        self.c_root = Customer.objects.create(
            name="Root Parrain",
            phone="0100000000",
        )
        self.c_n1 = Customer.objects.create(
            name="Client N1",
            phone="0100000001",
        )
        self.c_n2 = Customer.objects.create(
            name="Client N2",
            phone="0100000002",
        )
        self.c_n3 = Customer.objects.create(
            name="Client N3",
            phone="0100000003",
        )

        # Structure de parrainage :
        # root -> N1 -> N2 -> N3 (N3 passera la commande)
        self.l_root = ReferralLink.objects.create(
            customer=self.c_root,
            referral_code="ROOTCODE",
        )
        self.l_n1 = ReferralLink.objects.create(
            customer=self.c_n1,
            sponsor=self.l_root,
            referral_code="N1CODE",
        )
        self.l_n2 = ReferralLink.objects.create(
            customer=self.c_n2,
            sponsor=self.l_n1,
            referral_code="N2CODE",
        )
        self.l_n3 = ReferralLink.objects.create(
            customer=self.c_n3,
            sponsor=self.l_n2,
            referral_code="N3CODE",
        )

        # Petit catalogue de services
        self.cat = ServiceCategory.objects.create(
            name="Blanchisserie",
            slug="blanchisserie",
        )
        self.service = ServiceItem.objects.create(
            category=self.cat,
            name="Chemise lavage simple",
            default_price=Decimal("5000"),
        )

    def _create_done_order_for_customer(self, customer, total_ht: Decimal):
        """
        Création d'une commande 'done' pour un client donné,
        avec une seule ligne d'article dont le montant HT = total_ht.
        Puis génération explicite des commissions MLM via le service central.
        """
        from mlm.services import generate_mlm_commissions_for_order

        order = Order.objects.create(
            customer=customer,
            status="done",
        )

        # quantité et PU choisis de manière simple : total_ht = qty * price
        unit_price = self.service.default_price
        qty = int(total_ht / unit_price)

        OrderItem.objects.create(
            order=order,
            service=self.service,
            designation="Test article",
            quantity=qty,
            unit_price=unit_price,
        )

        # Re-save pour recalculer total + service_fee
        order.save()
        order.refresh_from_db()

        # 🔹 Génération explicite des commissions MLM (sans dépendre de Order.distribute_mlm_commissions)
        generate_mlm_commissions_for_order(order)

        return order


class MLMCommissionDistributionTests(MLMBaseTestCase):
    """
    Vérifie la distribution des 15 % de commission MLM :
    N1 = 10 %, N2 = 3 %, N3 = 2 % (sur le service_fee).
    """

    def test_commissions_15_percent_split_10_3_2(self):
        """
        Cas de base :
        - total_ht = 20 000 FCFA
        - service_fee = max(5 % de 20 000 = 1 000, min 500) -> 1 000 FCFA
        -> Commissions attendues :
           N1 : 10 % de 1 000 = 100
           N2 : 3 % de 1 000 = 30
           N3 : 2 % de 1 000 = 20
        """
        total_ht = Decimal("20000")
        order = self._create_done_order_for_customer(self.c_n3, total_ht)

        # On vérifie d'abord le service_fee
        self.assertEqual(order.service_fee, Decimal("1000.00"))

        commissions = ReferralCommission.objects.filter(order=order).order_by("level")
        self.assertEqual(commissions.count(), 3, "On doit avoir 3 commissions (N1, N2, N3).")

        # N1 (niveau 1) : 10 %
        c1 = commissions[0]
        self.assertEqual(c1.level, 1)
        self.assertEqual(c1.commission_amount, 100)

        # N2 (niveau 2) : 3 %
        c2 = commissions[1]
        self.assertEqual(c2.level, 2)
        self.assertEqual(c2.commission_amount, 30)

        # N3 (niveau 3) : 2 %
        c3 = commissions[2]
        self.assertEqual(c3.level, 3)
        self.assertEqual(c3.commission_amount, 20)

        # Vérification wallet transactions
        txs = WalletTransaction.objects.filter(order=order, type="mlm_commission")
        self.assertEqual(txs.count(), 3, "On doit avoir 3 mouvements de wallet.")

        total_wallet = sum((tx.amount for tx in txs), 0)
        self.assertEqual(
            total_wallet,
            150,
            "La somme des commissions doit faire 15 % du service_fee (150 FCFA).",
        )


class MLMServiceFeeMinimumTests(MLMBaseTestCase):
    """
    Vérifie que le minimum de service FAGNI (500 FCFA) est bien respecté
    même sur des petites commandes.
    """

    def test_service_fee_minimum_500_fcfa(self):
        """
        Pour un petit montant HT, 5 % < 500, donc service_fee doit être forcé à 500.
        Exemple : total_ht = 5 000
        - 5 % = 250 < 500 -> service_fee = 500
        """
        total_ht = Decimal("5000")
        order = self._create_done_order_for_customer(self.c_n3, total_ht)

        self.assertEqual(order.total_ht, total_ht)
        self.assertEqual(order.service_fee, Decimal("500.00"))

        # Commissions sur 500 FCFA :
        # 10 % = 50, 3 % = 15, 2 % = 10 -> 75 FCFA au total
        commissions = ReferralCommission.objects.filter(order=order).order_by("level")
        self.assertEqual(commissions.count(), 3)

        amounts = [c.commission_amount for c in commissions]
        self.assertIn(50, amounts)
        self.assertIn(15, amounts)
        self.assertIn(10, amounts)

        txs = WalletTransaction.objects.filter(order=order, type="mlm_commission")
        total_wallet = sum((tx.amount for tx in txs), 0)
        self.assertEqual(total_wallet, 75)


class MLMMultipleOrdersWalletTests(MLMBaseTestCase):
    """
    Vérifie que les wallets des parrains s'alimentent correctement
    sur plusieurs commandes.
    """

    def test_wallet_accumulates_commissions_on_multiple_orders(self):
        """
        On crée deux commandes pour le même client de niveau 3 (N3).
        On vérifie que :
        - il y a des transactions de wallet sur plusieurs commandes
        - la somme pour un parrain N1 correspond bien à la somme de ses commissions
        - la somme globale des commissions correspond bien à 15 % du service_fee cumulé.
        """
        # 1ère commande
        self._create_done_order_for_customer(self.c_n3, Decimal("20000"))
        # 2ème commande
        self._create_done_order_for_customer(self.c_n3, Decimal("20000"))

        # Pour N1 : on a 10 % du service_fee par commande.
        # Dans la configuration actuelle des tests, cela donne 30 FCFA par commande,
        # soit 60 FCFA au total sur 2 commandes.
        tx_n1 = WalletTransaction.objects.filter(
            profile=self.l_n1,
            type="mlm_commission",
        ).order_by("created_at")
        self.assertEqual(tx_n1.count(), 2)

        total_n1 = sum((tx.amount for tx in tx_n1), 0)
        self.assertEqual(total_n1, 60)

        # On doit avoir 3 commissions par commande (N1, N2, N3) → 6 au total
        total_commissions = ReferralCommission.objects.all().count()
        self.assertEqual(total_commissions, 6)

        # Contrôle global : la somme de toutes les commissions sur les deux commandes
        # doit faire 2 * (15 % de 1 000) = 2 * 150 = 300
        all_wallet_txs = WalletTransaction.objects.filter(type="mlm_commission")
        total_wallet_global = sum((tx.amount for tx in all_wallet_txs), 0)
        self.assertEqual(total_wallet_global, 300)
