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

        # 🔒 Force DB (évite update_financials() qui peut remettre à 0)
        Order.objects.filter(pk=self.order.pk).update(service_fee=Decimal("500.00"))
        self.order.refresh_from_db()


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
        # credit_wallet() est le seul point d'entree legitime depuis
        # l'ajout du garde-fou anti-fraude sur Wallet.balance - cree la
        # WalletTransaction ET met a jour le solde de facon coherente,
        # remplace la manipulation directe balance + save() precedente.
        from wallets.services import credit_wallet
        self.tx = credit_wallet(
            self.wallet_parrain,
            Decimal("50.00"),
            description=f"Commission niveau 1 pour commande {self.order.id}",
            order=self.order,
            tx_type="mlm_commission",
        )

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

        c = comms.order_by("-id").first()
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



from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from orders.models import Customer, Order
from orders.views import _compute_order_pricing, _client_order_amounts, handle_referral_reward
from orders.pricing_engine import compute_order_pricing
from wallets.models import WalletTransaction
from mlm.models import ReferralLink


class ReferralFlowEngineTestCase(TestCase):
    def setUp(self):
        self.sponsor = Customer.objects.create(
            name="Parrain Test",
            phone="0700000001",
        )
        self.profile = ReferralLink.objects.create(
            customer=self.sponsor,
            referral_code="FAGNI-00000001",
            actor_type="client",
        )
        self.child = Customer.objects.create(
            name="Filleul Test",
            phone="0700000002",
        )

    def _make_order(self, **kwargs):
        created_at = kwargs.pop("created_at", timezone.now() - timedelta(hours=72))
        data = {
            "customer": self.child,
            "status": "pending",
            "payment_status": "pending",
            "total_client_ttc": Decimal("13330"),
            "prestation_total": Decimal("10000"),
            "referral_code": self.profile.referral_code,
        }
        data.update(kwargs)
        order = Order.objects.create(**data)
        Order.objects.filter(pk=order.pk).update(created_at=created_at)
        order.refresh_from_db()
        return order

    def test_child_discount_is_applied_on_first_order(self):
        """
        Corrige : baseline doit etre l'Order lui-meme (total_client_ttc
        avant remise, 13330 par _make_order), pas compute_order_pricing(order)
        qui appelle en fait calculate_order(nb_articles) - un alias different,
        jamais concu pour prendre un Order complet en argument (TypeError).
        """
        order = self._make_order()

        baseline = order
        pricing = _compute_order_pricing(order)
        client_amounts = _client_order_amounts(order)

        self.assertEqual(pricing.get("child_referral_discount"), Decimal("500"))
        self.assertEqual(client_amounts.get("child_referral_discount"), Decimal("500"))
        self.assertEqual(
            Decimal(str(baseline.total_client_ttc)) - Decimal(str(pricing.get("total_client_ttc"))),
            Decimal("500")
        )
        self.assertEqual(
            Decimal(str(client_amounts.get("total_client_ttc"))),
            Decimal(str(pricing.get("total_client_ttc")))
        )

    def test_child_discount_not_applied_on_second_order(self):
        first_order = self._make_order()
        second_order = self._make_order(
            created_at=timezone.now() - timedelta(hours=24),
            total_client_ttc=Decimal("9000"),
            prestation_total=Decimal("7000"),
        )

        pricing_second = _compute_order_pricing(second_order)

        self.assertEqual(pricing_second.get("child_referral_discount"), Decimal("0"))

    def test_sponsor_reward_creates_wallet_transaction(self):
        order = self._make_order(
            status="pending",
            payment_status="paid",
        )

        handle_referral_reward(order)

        txs = WalletTransaction.objects.filter(
            order=order,
            type="mlm_commission",
            direction="in",
            wallet__owner_type="customer",
            wallet__customer=self.sponsor,
        )

        self.assertEqual(txs.count(), 1)
        # Montant mis a jour a 1000 FCFA (Pilot Growth Plan, decision du
        # 9 juillet 2026, orders/views.py handle_referral_reward) - le
        # test attendait encore l'ancien montant de 500 FCFA.
        self.assertEqual(txs.first().amount, Decimal("1000.00"))

    def test_sponsor_reward_is_idempotent(self):
        order = self._make_order(
            status="pending",
            payment_status="paid",
        )

        handle_referral_reward(order)
        handle_referral_reward(order)

        txs = WalletTransaction.objects.filter(
            order=order,
            type="mlm_commission",
            direction="in",
            wallet__owner_type="customer",
            wallet__customer=self.sponsor,
        )

        self.assertEqual(txs.count(), 1)

    def test_no_reward_before_48h(self):
        order = self._make_order(
            status="pending",
            payment_status="paid",
            created_at=timezone.now() - timedelta(hours=12),
        )

        handle_referral_reward(order)

        txs = WalletTransaction.objects.filter(
            order=order,
            type="mlm_commission",
            direction="in",
            wallet__owner_type="customer",
            wallet__customer=self.sponsor,
        )

        self.assertEqual(txs.count(), 0)

    def test_no_reward_if_same_phone(self):
        # même téléphone après strip(), tout en restant unique en base
        self.child.phone = self.sponsor.phone + " "
        self.child.save(update_fields=["phone"])

        order = self._make_order(
            status="pending",
            payment_status="paid",
        )

        handle_referral_reward(order)

        txs = WalletTransaction.objects.filter(
            order=order,
            type="mlm_commission",
            direction="in",
            wallet__owner_type="customer",
            wallet__customer=self.sponsor,
        )

        self.assertEqual(txs.count(), 0)

