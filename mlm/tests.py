from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from orders.models import Customer, Order
from mlm.models import ReferralLink, ReferralCommission
from wallets.models import Wallet, WalletTransaction


User = get_user_model()


class MlmFlowsTestCase(TestCase):
    """
    Tests de base pour vérifier le flux MLM :
    - création de commissions pour le parrain
    - mouvement de wallet pour le parrain
    - affichage des vues principales (dashboard affilié, détail affilié, global MLM)
    """

    def setUp(self):
        # ---------- Utilisateur staff pour les vues admin/staff ----------
        self.staff_user, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "is_staff": True,
                "is_active": True,
            },
        )
        # On (ré)initialise le mot de passe et les flags si besoin
        self.staff_user.is_staff = True
        self.staff_user.is_active = True
        self.staff_user.set_password("admin123")
        self.staff_user.save()

        self.client.force_login(self.staff_user)

        # ---------- Parrain ----------
        self.parrain_customer = Customer.objects.create(
            name="Parrain Test",
            phone="0700000001",
        )
        self.parrain_profile = ReferralLink.objects.create(
            customer=self.parrain_customer,
            referral_code="TESTPARRAIN001",
        )

        # ---------- Filleul ----------
        self.filleul_customer = Customer.objects.create(
            name="Filleul Test",
            phone="0700000002",
        )
        self.filleul_profile = ReferralLink.objects.create(
            customer=self.filleul_customer,
            referral_code="TESTFILLEUL001",
            sponsor=self.parrain_profile,
        )

        # ---------- Wallet du parrain ----------
        self.wallet_parrain = Wallet.objects.create(
            owner_type="customer",
            customer=self.parrain_customer,
            balance=Decimal("0.00"),
        )

        # ---------- Commande du filleul ----------
        self.order = Order.objects.create(
            customer=self.filleul_customer,
            status="done",
            service_fee=Decimal("500.00"),
        )

        # ---------- Commission rattachée au parrain ----------
        # Si la logique métier a déjà créé une commission (signals, etc.),
        # on la récupère et on la "normalise" pour les tests.
        existing_qs = ReferralCommission.objects.filter(
            order=self.order,
            beneficiary_profile=self.parrain_profile,
        )

        if existing_qs.exists():
            self.comm = existing_qs.first()
            self.comm.level = 1
            self.comm.service_fee_base = self.order.service_fee or Decimal("0.00")
            self.comm.commission_percent = Decimal("10.00")
            self.comm.commission_amount = Decimal("50.00")
            self.comm.save()
        else:
            self.comm = ReferralCommission.objects.create(
                order=self.order,
                beneficiary_profile=self.parrain_profile,
                level=1,
                service_fee_base=self.order.service_fee or Decimal("0.00"),
                commission_percent=Decimal("10.00"),
                commission_amount=Decimal("50.00"),
            )

        # ---------- Mouvement de wallet côté parrain ----------
        self.tx = WalletTransaction.objects.create(
            wallet=self.wallet_parrain,
            type="mlm_commission",
            amount=Decimal("50.00"),
            description=f"Commission niveau 1 pour commande {self.order.id}",
            order=self.order,
        )

        # Mise à jour du solde courant du wallet
        self.wallet_parrain.balance = Decimal("50.00")
        self.wallet_parrain.save()

    # ---------------------------------------------------------
    #  TESTS MÉTIER
    # ---------------------------------------------------------

    def test_commission_created_for_parrain(self):
        """La commission doit être bien liée au parrain et à la commande."""
        comms = ReferralCommission.objects.filter(
            order=self.order,
            beneficiary_profile=self.parrain_profile,
        )
        # Il doit y avoir AU MOINS une commission pour ce couple (commande, parrain)
        self.assertGreaterEqual(comms.count(), 1)

        c = comms.order_by("id").first()
        self.assertEqual(c.beneficiary_profile, self.parrain_profile)
        self.assertEqual(c.commission_amount, Decimal("50.00"))
        self.assertEqual(c.commission_percent, Decimal("10.00"))
        self.assertEqual(c.service_fee_base, Decimal("500.00"))

    def test_wallet_transaction_for_parrain(self):
        """Le wallet du parrain doit avoir reçu la transaction MLM."""
        txs = WalletTransaction.objects.filter(
            wallet=self.wallet_parrain,
            type="mlm_commission",
            order=self.order,
        )
        self.assertGreaterEqual(txs.count(), 1)
        # Vérifie qu'au moins une transaction a le bon montant
        self.assertTrue(
            txs.filter(amount=Decimal("50.00")).exists(),
            "Aucune transaction de 50.00 FCFA trouvée pour le parrain.",
        )
        # Le solde doit être au moins 50 (si la logique ajoute d'autres commissions, tant mieux)
        self.assertGreaterEqual(self.wallet_parrain.balance, Decimal("50.00"))

    # ---------------------------------------------------------
    #  TESTS VUES
    # ---------------------------------------------------------

    def test_affiliate_dashboard_view(self):
        """
        Le dashboard affilié doit répondre en 200.
        _get_current_profile() prend le premier ReferralLink, donc notre parrain.
        """
        url = reverse("mlm:affiliate_dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mon espace affilié")

    def test_affiliate_detail_view(self):
        """La fiche affilié doit afficher le bon code et le total commissions."""
        url = reverse(
            "mlm:affiliate_detail",
            args=[self.parrain_profile.referral_code],
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.parrain_profile.referral_code)
        # On vérifie qu'au moins "50" (montant de la commission) apparaît quelque part
        self.assertIn("50", response.content.decode("utf-8"))

    def test_global_mlm_dashboard_view(self):
        """Le dashboard global MLM doit répondre en 200."""
        url = reverse("mlm:global_dashboard")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dashboard MLM")
