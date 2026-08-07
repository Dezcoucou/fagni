from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from orders.models import (
    Customer,
    Order,
    OrderItem,
    OrderPaymentEvent,
    Payment,
)
from orders.views import apply_order_payment
from partners.models import LaundryPartner


class PaymentVoidTestBase(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client annulation paiement",
            phone="0700998811",
            address="Riviera 3",
        )

        self.laundry = (
            LaundryPartner.objects.filter(is_active=True).first()
            or LaundryPartner.objects.create(
                name="Pressing sécurité paiement",
                address="Riviera 3",
                latitude=Decimal("5.360000"),
                longitude=Decimal("-3.950000"),
                is_active=True,
            )
        )

    def make_order(self, *, total=Decimal("6000")):
        order = Order.objects.create(
            customer=self.customer,
            laundry_partner=self.laundry,
            status="pending",
            payment_status="pending",
            pricing_mode="item",
        )

        OrderItem.objects.create(
            order=order,
            designation="Articles test",
            quantity=1,
            unit_price=total,
            total=total,
        )

        order.update_financials(save=True)
        order.refresh_from_db()

        self.assertGreater(
            Decimal(str(order.total_client_ttc or 0)),
            Decimal("0"),
        )

        return order


class PaymentAccountingStatusTests(PaymentVoidTestBase):
    @patch(
        "orders.models.Order.mark_as_paid_and_distribute",
        return_value=None,
    )
    def test_nouveau_paiement_est_confirme_par_defaut(
        self,
        _mock_distribute,
    ):
        order = self.make_order()

        payment = Payment.objects.create(
            order=order,
            amount=Decimal("1000"),
            channel="manual",
            reference="PAYMENT-CONFIRMED-DEFAULT",
            source="system",
        )

        self.assertEqual(payment.status, "confirmed")
        self.assertIsNone(payment.voided_at)
        self.assertEqual(payment.voided_reason, "")

    @patch(
        "orders.models.Order.mark_as_paid_and_distribute",
        return_value=None,
    )
    def test_total_paid_exclut_un_paiement_annule(
        self,
        _mock_distribute,
    ):
        order = self.make_order()

        confirmed = Payment.objects.create(
            order=order,
            amount=Decimal("1000"),
            channel="manual",
            reference="PAYMENT-CONFIRMED-001",
            source="system",
        )

        voided = Payment.objects.create(
            order=order,
            amount=Decimal("2000"),
            channel="manual",
            reference="PAYMENT-VOIDED-001",
            source="system",
        )

        Payment.objects.filter(pk=voided.pk).update(
            status="voided",
        )

        order.refresh_from_db()

        self.assertEqual(
            order.total_paid_from_payments(),
            confirmed.amount,
        )

    @patch(
        "orders.models.Order.mark_as_paid_and_distribute",
        return_value=None,
    )
    def test_resynchronisation_ignore_un_paiement_annule(
        self,
        _mock_distribute,
    ):
        order = self.make_order()

        payment = Payment.objects.create(
            order=order,
            amount=order.total_client_ttc,
            channel="manual",
            reference="PAYMENT-FULL-THEN-VOID",
            source="system",
        )

        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")

        Payment.objects.filter(pk=payment.pk).update(
            status="voided",
        )

        order.sync_payment_status_from_payments(save=False)

        self.assertEqual(order.amount_paid, Decimal("0"))
        self.assertEqual(order.payment_status, "pending")


class PaymentReferenceSecurityTests(PaymentVoidTestBase):
    @patch(
        "orders.models.Order.mark_as_paid_and_distribute",
        return_value=None,
    )
    def test_reference_wave_ne_peut_pas_etre_reutilisee_sur_autre_commande(
        self,
        _mock_distribute,
    ):
        first_order = self.make_order()
        second_order = self.make_order()

        apply_order_payment(
            first_order,
            Decimal("1000"),
            channel="wave_webhook",
            reference="WAVE-GLOBAL-REFERENCE-001",
        )

        with self.assertRaises(ValidationError):
            apply_order_payment(
                second_order,
                Decimal("1000"),
                channel="wave_webhook",
                reference="WAVE-GLOBAL-REFERENCE-001",
            )

        self.assertEqual(
            Payment.objects.filter(
                reference="WAVE-GLOBAL-REFERENCE-001",
            ).count(),
            1,
        )

    def test_canal_invente_est_refuse(self):
        order = self.make_order()

        with self.assertRaises(ValidationError):
            apply_order_payment(
                order,
                Decimal("1000"),
                channel="canal_totalement_invente",
                reference="FAKE-CHANNEL-REFERENCE",
                note="Un canal inconnu ne doit jamais créer un Payment.",
            )

        self.assertFalse(
            Payment.objects.filter(order=order).exists()
        )

    @patch(
        "orders.models.Order.mark_as_paid_and_distribute",
        return_value=None,
    )
    def test_canal_wave_ops_exige_une_reference(
        self,
        _mock_distribute,
    ):
        order = self.make_order()

        with self.assertRaises(ValidationError):
            apply_order_payment(
                order,
                Decimal("1000"),
                channel="wave_ops",
                reference="",
            )

        self.assertFalse(
            Payment.objects.filter(order=order).exists()
        )


class PaymentVoidAuditTests(PaymentVoidTestBase):
    @patch(
        "orders.models.Order.mark_as_paid_and_distribute",
        return_value=None,
    )
    def test_annulation_future_devra_conserver_evenement_original(
        self,
        _mock_distribute,
    ):
        order = self.make_order()

        apply_order_payment(
            order,
            Decimal("1000"),
            channel="manual",
            reference="AUDIT-PAYMENT-001",
            note="Paiement initial",
        )

        self.assertEqual(
            OrderPaymentEvent.objects.filter(
                order=order,
                reference="AUDIT-PAYMENT-001",
            ).count(),
            1,
        )

        original_event = OrderPaymentEvent.objects.get(
            order=order,
            reference="AUDIT-PAYMENT-001",
        )

        self.assertEqual(original_event.amount, Decimal("1000"))
        self.assertEqual(original_event.note, "Paiement initial")


class PaymentVoidServiceTests(TestCase):
    def setUp(self):
        from decimal import Decimal
        from orders.models import Customer, Order, OrderItem
        from partners.models import LaundryPartner, DeliveryPartner

        self.customer = Customer.objects.create(
            name="Client void paiement",
            phone="0700123499",
            address="Riviera 3",
        )

        self.laundry = LaundryPartner.objects.create(
            name="Pressing void test",
            address="Riviera 3",
            latitude=Decimal("5.360000"),
            longitude=Decimal("-3.950000"),
            is_active=True,
        )

        self.driver = DeliveryPartner.objects.create(
            name="Livreur void test",
            phone="0700112233",
            latitude=Decimal("5.360000"),
            longitude=Decimal("-3.950000"),
            is_active=True,
        )

        self.order = Order.objects.create(
            customer=self.customer,
            laundry_partner=self.laundry,
            pickup_driver=self.driver,
            status="ready",
            pricing_mode="item",
        )

        OrderItem.objects.create(
            order=self.order,
            designation="Article test",
            quantity=1,
            unit_price=Decimal("9500"),
            total=Decimal("9500"),
        )

        self.order.update_financials(save=True)
        self.order.refresh_from_db()

    @patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None)
    def test_void_payment_exclut_paiement_du_total_comptable(
        self,
        _mock_distribute,
    ):
        from decimal import Decimal
        from orders.models import Payment
        from orders.views import apply_order_payment

        apply_order_payment(
            self.order,
            Decimal("9500"),
            channel="manual",
            reference="FALSE-PAYMENT-001",
        )

        payment = Payment.objects.get(
            order=self.order,
            reference="FALSE-PAYMENT-001",
        )

        Payment.objects.filter(pk=payment.pk).update(
            status=Payment.ACCOUNTING_STATUS_VOIDED,
            voided_reason="Paiement confirmé par erreur",
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.total_paid_from_payments(),
            Decimal("0"),
        )

    @patch("orders.models.Order.mark_as_paid_and_distribute", return_value=None)
    def test_void_payment_ne_doit_pas_supprimer_etat_ready(
        self,
        _mock_distribute,
    ):
        from decimal import Decimal
        from orders.models import Payment
        from orders.views import apply_order_payment

        apply_order_payment(
            self.order,
            Decimal("9500"),
            channel="manual",
            reference="FALSE-PAYMENT-READY",
        )

        payment = Payment.objects.get(
            order=self.order,
            reference="FALSE-PAYMENT-READY",
        )

        Payment.objects.filter(pk=payment.pk).update(
            status=Payment.ACCOUNTING_STATUS_VOIDED,
        )

        self.order.refresh_from_db()

        self.assertEqual(self.order.status, "ready")

    def test_void_payment_service_existe(self):
        try:
            from orders.services import void_order_payment
        except ImportError as exc:
            self.fail(
                f"void_order_payment doit exister dans orders.services : {exc}"
            )

        self.assertTrue(callable(void_order_payment))



class PaymentVoidExecutionTests(TestCase):
    def setUp(self):
        from decimal import Decimal

        from orders.models import Customer, Order, OrderItem
        from partners.models import LaundryPartner

        self.customer = Customer.objects.create(
            name="Client execution void",
            phone="0700999911",
            address="Riviera 3",
        )

        self.laundry = LaundryPartner.objects.create(
            name="Pressing execution void",
            address="Riviera 3",
            latitude=Decimal("5.360000"),
            longitude=Decimal("-3.950000"),
            is_active=True,
        )

        self.order = Order.objects.create(
            customer=self.customer,
            laundry_partner=self.laundry,
            status="pending",
            pricing_mode="item",
        )

        OrderItem.objects.create(
            order=self.order,
            designation="Article execution void",
            quantity=1,
            unit_price=Decimal("5000"),
            total=Decimal("5000"),
        )

        self.order.update_financials(save=True)
        self.order.refresh_from_db()

    def _create_paid_order(self, reference="VOID-EXEC-001"):
        from unittest.mock import patch

        from orders.views import apply_order_payment

        amount = self.order.total_client_ttc

        with patch(
            "orders.models.Order.mark_as_paid_and_distribute",
            return_value=None,
        ):
            result = apply_order_payment(
                self.order,
                amount,
                channel="manual",
                reference=reference,
                note="Paiement test avant annulation",
            )

        self.order.refresh_from_db()

        return result

    def test_void_service_annule_reellement_payment_et_recalcule_commande(self):
        from decimal import Decimal

        from orders.models import Payment
        from orders.services import void_order_payment

        self._create_paid_order("VOID-EXEC-ACCOUNTING")

        payment = Payment.objects.get(
            order=self.order,
            reference="VOID-EXEC-ACCOUNTING",
        )

        self.assertEqual(
            payment.status,
            Payment.ACCOUNTING_STATUS_CONFIRMED,
        )

        self.assertEqual(self.order.payment_status, "paid")

        result = void_order_payment(
            payment.id,
            reason="Paiement confirmé par erreur",
        )

        payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertTrue(result["voided"])
        self.assertFalse(result["already_voided"])

        self.assertEqual(
            payment.status,
            Payment.ACCOUNTING_STATUS_VOIDED,
        )
        self.assertIsNotNone(payment.voided_at)
        self.assertEqual(
            payment.voided_reason,
            "Paiement confirmé par erreur",
        )

        self.assertEqual(
            self.order.amount_paid,
            Decimal("0"),
        )
        self.assertEqual(
            self.order.payment_status,
            "pending",
        )
        self.assertIsNone(self.order.payment_date)

    def test_void_service_contre_passe_cashback(self):
        from decimal import Decimal

        from orders.models import Payment
        from orders.services import void_order_payment
        from wallets.models import WalletTransaction

        self._create_paid_order("VOID-EXEC-CASHBACK")

        payment = Payment.objects.get(
            order=self.order,
            reference="VOID-EXEC-CASHBACK",
        )

        cashback = (
            WalletTransaction.objects
            .filter(
                order=self.order,
                wallet__owner_type="customer",
                type="credit",
                direction="in",
                description__startswith="Cashback FAGNI commande",
            )
            .first()
        )

        self.assertIsNotNone(cashback)

        expected_cashback = (
            Decimal(str(self.order.total_client_ttc))
            * Decimal("0.02")
        ).quantize(Decimal("0.01"))

        self.assertEqual(
            cashback.amount,
            expected_cashback,
        )

        result = void_order_payment(
            payment.id,
            reason="Paiement erroné cashback",
        )

        reversal = WalletTransaction.objects.get(
            idempotency_key=f"payment_void:cashback:{payment.id}",
        )

        self.assertTrue(result["cashback_reversed"])

        self.assertEqual(
            reversal.wallet_id,
            cashback.wallet_id,
        )
        self.assertEqual(
            reversal.direction,
            "out",
        )
        self.assertEqual(
            reversal.type,
            "adjustment",
        )
        self.assertEqual(
            reversal.amount,
            cashback.amount,
        )

        cashback.wallet.refresh_from_db()

        self.assertEqual(
            cashback.wallet.balance,
            Decimal("0.00"),
        )

    def test_void_service_preserve_ready_et_wash_complete_time(self):
        from django.utils import timezone

        from orders.models import Order, Payment
        from orders.services import void_order_payment

        self._create_paid_order("VOID-EXEC-READY")

        ready_time = timezone.now()

        Order.objects.filter(pk=self.order.pk).update(
            status="ready",
            wash_complete_time=ready_time,
        )

        payment = Payment.objects.get(
            order=self.order,
            reference="VOID-EXEC-READY",
        )

        void_order_payment(
            payment.id,
            reason="Paiement erroné linge déjà prêt",
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.status,
            "ready",
        )

        self.assertEqual(
            self.order.wash_complete_time,
            ready_time,
        )

        self.assertEqual(
            self.order.payment_status,
            "pending",
        )

    def test_void_service_est_idempotent(self):
        from orders.models import OrderPaymentEvent, Payment
        from orders.services import void_order_payment
        from wallets.models import WalletTransaction

        self._create_paid_order("VOID-EXEC-IDEMPOTENT")

        payment = Payment.objects.get(
            order=self.order,
            reference="VOID-EXEC-IDEMPOTENT",
        )

        first = void_order_payment(
            payment.id,
            reason="Première annulation",
        )

        reversal_key = f"payment_void:cashback:{payment.id}"

        reversal_count_before = (
            WalletTransaction.objects
            .filter(idempotency_key=reversal_key)
            .count()
        )

        audit_count_before = (
            OrderPaymentEvent.objects
            .filter(
                order=self.order,
                reference=f"VOID-PAYMENT-{payment.id}",
            )
            .count()
        )

        second = void_order_payment(
            payment.id,
            reason="Deuxième tentative",
        )

        reversal_count_after = (
            WalletTransaction.objects
            .filter(idempotency_key=reversal_key)
            .count()
        )

        audit_count_after = (
            OrderPaymentEvent.objects
            .filter(
                order=self.order,
                reference=f"VOID-PAYMENT-{payment.id}",
            )
            .count()
        )

        self.assertTrue(first["voided"])
        self.assertFalse(first["already_voided"])

        self.assertFalse(second["voided"])
        self.assertTrue(second["already_voided"])

        self.assertEqual(
            reversal_count_before,
            1,
        )
        self.assertEqual(
            reversal_count_after,
            1,
        )

        self.assertEqual(
            audit_count_before,
            1,
        )
        self.assertEqual(
            audit_count_after,
            1,
        )

    def test_void_service_ne_supprime_jamais_payment_original(self):
        from orders.models import Payment
        from orders.services import void_order_payment

        self._create_paid_order("VOID-EXEC-AUDIT")

        payment = Payment.objects.get(
            order=self.order,
            reference="VOID-EXEC-AUDIT",
        )

        payment_id = payment.id
        original_amount = payment.amount
        original_reference = payment.reference

        void_order_payment(
            payment.id,
            reason="Conservation preuve comptable",
        )

        self.assertTrue(
            Payment.objects.filter(pk=payment_id).exists()
        )

        payment.refresh_from_db()

        self.assertEqual(
            payment.amount,
            original_amount,
        )
        self.assertEqual(
            payment.reference,
            original_reference,
        )
        self.assertEqual(
            payment.status,
            Payment.ACCOUNTING_STATUS_VOIDED,
        )


class WaveVerificationHardeningTests(TestCase):
    """
    Régressions P0 après incident Payment #22.

    Une référence ou un libellé saisi/fabriqué par FAGNI ne constitue
    jamais une preuve de paiement Wave.
    """

    def setUp(self):
        from decimal import Decimal

        from orders.models import Customer, Order, OrderItem

        self.customer = Customer.objects.create(
            name="Client sécurité Wave",
            phone="0700887766",
            address="Riviera 3",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status="pending",
            payment_status="pending",
            pricing_mode="item",
        )

        OrderItem.objects.create(
            order=self.order,
            designation="Article sécurité Wave",
            quantity=1,
            unit_price=Decimal("5000"),
            total=Decimal("5000"),
        )

        self.order.update_financials(save=True)
        self.order.refresh_from_db()

    def test_webhook_ne_doît_plus_rattacher_via_payment_declared_reference(self):
        """
        payment_declared_reference est une donnée déclarative/legacy,
        jamais une clé de rattachement PSP.
        """
        import json

        from orders.models import Payment
        from django.test import override_settings
        from django.urls import reverse

        self.order.payment_declared_reference = "checkout_test_legacy_forbidden"
        self.order.wave_checkout_id = ""
        self.order.save(
            update_fields=[
                "payment_declared_reference",
                "wave_checkout_id",
            ]
        )

        payload = {
            "id": "evt_legacy_forbidden",
            "type": "checkout.session.completed",
            "data": {
                "id": "checkout_test_legacy_forbidden",
                "payment_status": "succeeded",
                "checkout_status": "complete",
                "currency": "XOF",
                "amount": "5000",
            },
        }

        with override_settings(DEBUG=True):
            response = self.client.post(
                reverse("orders:wave_webhook"),
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Payment.objects.filter(order=self.order).exists()
        )

    def test_wave_checkout_id_reste_seule_cle_de_rattachement_webhook(self):
        import json

        from orders.models import Payment
        from django.test import override_settings
        from django.urls import reverse

        self.order.wave_checkout_id = "checkout_test_canonical_only"
        self.order.save(update_fields=["wave_checkout_id"])

        payload = {
            "id": "evt_canonical_only",
            "type": "checkout.session.completed",
            "data": {
                "id": "checkout_test_canonical_only",
                "payment_status": "succeeded",
                "checkout_status": "complete",
                "currency": "XOF",
                "amount": "5000",
            },
        }

        with override_settings(DEBUG=True):
            response = self.client.post(
                reverse("orders:wave_webhook"),
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            Payment.objects.filter(
                order=self.order,
                reference="checkout_test_canonical_only",
                status=Payment.ACCOUNTING_STATUS_CONFIRMED,
            ).exists()
        )

    def test_reference_wave_reste_reservee_meme_si_payment_est_voided(self):
        """
        L'annulation comptable ne doit jamais rendre réutilisable
        un identifiant PSP Wave déjà consommé.
        """
        from decimal import Decimal

        from django.core.exceptions import ValidationError

        from orders.models import Customer, Order, OrderItem, Payment
        from orders.views import apply_order_payment

        apply_order_payment(
            self.order,
            Decimal("1000"),
            channel="wave_webhook",
            reference="WAVE-IMMUTABLE-REFERENCE",
        )

        first_payment = Payment.objects.get(
            order=self.order,
            reference="WAVE-IMMUTABLE-REFERENCE",
        )

        Payment.objects.filter(pk=first_payment.pk).update(
            status=Payment.ACCOUNTING_STATUS_VOIDED,
        )

        customer_2 = Customer.objects.create(
            name="Autre client Wave",
            phone="0700887767",
            address="Riviera 3",
        )

        order_2 = Order.objects.create(
            customer=customer_2,
            status="pending",
            payment_status="pending",
            pricing_mode="item",
        )

        OrderItem.objects.create(
            order=order_2,
            designation="Deuxième article",
            quantity=1,
            unit_price=Decimal("5000"),
            total=Decimal("5000"),
        )

        order_2.update_financials(save=True)
        order_2.refresh_from_db()

        with self.assertRaises(ValidationError):
            apply_order_payment(
                order_2,
                Decimal("1000"),
                channel="wave_webhook",
                reference="WAVE-IMMUTABLE-REFERENCE",
            )

    def test_helper_verification_wave_existe(self):
        try:
            from orders.services import verify_wave_checkout_session
        except ImportError as exc:
            self.fail(
                "verify_wave_checkout_session doit exister dans "
                f"orders.services : {exc}"
            )

        self.assertTrue(callable(verify_wave_checkout_session))


class WaveOpsVerificationTests(TestCase):
    """
    Toute confirmation Wave effectuée par OPS/Admin doit être adossée
    à une vraie session Wave stockée dans Order.wave_checkout_id et
    vérifiée serveur-à-serveur.

    Une référence fabriquée localement n'est jamais une preuve de paiement.
    """

    def setUp(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        from orders.models import Customer, Order, OrderItem

        self.customer = Customer.objects.create(
            name="Client OPS Wave sécurisé",
            phone="0700665544",
            address="Riviera 3",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status="pending",
            payment_status="declared",
            payment_verification_status="pending_review",
            pricing_mode="item",
            payment_declared_reference="REFERENCE-SAISIE-CLIENT",
        )

        OrderItem.objects.create(
            order=self.order,
            designation="Article test",
            quantity=1,
            unit_price=Decimal("5000"),
            total=Decimal("5000"),
        )

        self.order.update_financials(save=True)

        User = get_user_model()

        self.staff = User.objects.create_user(
            username="ops_wave_test",
            password="test-pass-123",
            is_staff=True,
        )

        self.client.force_login(self.staff)

    def test_ops_confirm_wave_sans_wave_checkout_id_ne_cree_aucun_payment(self):
        from django.urls import reverse
        from orders.models import Payment

        response = self.client.post(
            reverse(
                "orders:ops_order_confirm_wave_paid",
                args=[self.order.id],
            )
        )

        self.assertFalse(
            Payment.objects.filter(order=self.order).exists()
        )

        self.order.refresh_from_db()
        self.assertNotEqual(self.order.payment_status, "paid")

    def test_admin_review_sans_wave_checkout_id_ne_cree_aucun_payment(self):
        from orders.models import Payment
        from orders.views import admin_payment_review_confirm

        request = self.client.post(
            f"/admin/payment-review/{self.order.id}/confirm/"
        ).wsgi_request

        request.user = self.staff

        admin_payment_review_confirm(
            request,
            self.order.id,
        )

        self.assertFalse(
            Payment.objects.filter(order=self.order).exists()
        )

        self.order.refresh_from_db()
        self.assertNotEqual(self.order.payment_status, "paid")

    def test_admin_confirm_declared_sans_wave_checkout_id_ne_cree_aucun_payment(self):
        from orders.models import Payment
        from orders.views import admin_confirm_declared_payment

        request = self.client.post(
            f"/orders/admin/confirm-payment/{self.order.id}/"
        ).wsgi_request

        request.user = self.staff

        # Les messages Django peuvent être utilisés par la vue.
        from django.contrib.messages.storage.fallback import FallbackStorage
        setattr(request, "session", self.client.session)
        setattr(request, "_messages", FallbackStorage(request))

        admin_confirm_declared_payment(
            request,
            self.order.id,
        )

        self.assertFalse(
            Payment.objects.filter(order=self.order).exists()
        )

        self.order.refresh_from_db()
        self.assertNotEqual(self.order.payment_status, "paid")


class WaveManualVerificationWithoutApiTests(TestCase):
    """
    Sécurité Wave provisoire sans API PSP.

    Une déclaration client n'est jamais un paiement.
    Une référence Wave saisie par OPS n'est jamais une preuve suffisante.
    Seule une vérification humaine explicite doit pouvoir produire
    un Payment wave_manual_verified.
    """

    def setUp(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model
        from orders.models import Customer, Order, OrderItem

        self.customer = Customer.objects.create(
            name="Client Wave manuel",
            phone="0700554433",
            address="Riviera 3",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status="done",
            payment_status="pending",
            pricing_mode="item",
        )

        OrderItem.objects.create(
            order=self.order,
            designation="Test paiement manuel Wave",
            quantity=1,
            unit_price=Decimal("5000"),
            total=Decimal("5000"),
        )

        self.order.total_client_ttc = Decimal("5000")
        self.order.save(update_fields=["total_client_ttc"])

        User = get_user_model()
        self.staff = User.objects.create_user(
            username="ops_wave_manual_test",
            password="test-password",
            is_staff=True,
        )

    def test_ops_mark_paid_ne_peut_plus_valider_wave_avec_reference_libre(self):
        import json
        from django.urls import reverse
        from orders.models import Payment
        from orders.tests.test_wave_checkout import _ops_headers

        response = self.client.post(
            reverse("api-ops-mark-paid", args=[self.order.id]),
            data=json.dumps({
                "channel": "wave",
                "reference": "FAUSSE-REFERENCE-WAVE",
            }),
            content_type="application/json",
            **_ops_headers(),
        )

        self.order.refresh_from_db()

        self.assertNotEqual(self.order.payment_status, "paid")
        self.assertEqual(self.order.amount_paid, 0)
        self.assertFalse(
            Payment.objects.filter(
                order=self.order,
                reference="FAUSSE-REFERENCE-WAVE",
            ).exists()
        )

    def test_apply_order_payment_accepte_wave_manual_verified(self):
        from decimal import Decimal
        from orders.views import apply_order_payment

        result = apply_order_payment(
            self.order,
            Decimal("5000"),
            channel="wave_manual_verified",
            reference="WAVE-MANUAL-REAL-001",
            note="Transaction vérifiée manuellement dans Wave",
        )

        self.order.refresh_from_db()

        self.assertEqual(result["payment_status"], "paid")
        self.assertEqual(self.order.payment_status, "paid")
        self.assertEqual(self.order.amount_paid, Decimal("5000"))

    def test_wave_manual_verified_exige_reference(self):
        from decimal import Decimal
        from django.core.exceptions import ValidationError
        from orders.views import apply_order_payment

        with self.assertRaises(ValidationError):
            apply_order_payment(
                self.order,
                Decimal("5000"),
                channel="wave_manual_verified",
                reference="",
                note="Doit échouer",
            )

    def test_declaration_client_ne_cree_toujours_pas_payment(self):
        from orders.models import Payment

        self.order.payment_status = "declared"
        self.order.payment_verification_status = "pending_review"
        self.order.payment_declared_reference = "REFERENCE-CLIENT-001"
        self.order.save(update_fields=[
            "payment_status",
            "payment_verification_status",
            "payment_declared_reference",
        ])

        self.order.refresh_from_db()

        self.assertEqual(self.order.amount_paid, 0)
        self.assertFalse(
            Payment.objects.filter(order=self.order).exists()
        )


class OrderPaymentProjectionSecurityTests(PaymentVoidTestBase):
    """
    Order.amount_paid et Order.payment_status sont des projections.

    Order.save() ne doit jamais transformer à lui seul un état partial
    en paid sur la simple base de amount_paid.
    """

    def test_order_save_ne_promeut_pas_partial_en_paid_sans_payment(self):
        from decimal import Decimal
        from orders.models import Order, Payment

        order = self.make_order()

        total = Decimal(str(order.total_client_ttc or 0))
        self.assertGreater(total, Decimal("0"))

        self.assertFalse(
            Payment.objects.filter(order=order).exists()
        )

        # Simule un état legacy/incohérent présent en base :
        # amount_paid == total mais aucun Payment ne le justifie.
        Order.objects.filter(pk=order.pk).update(
            payment_status="partial",
            amount_paid=total,
        )

        order.refresh_from_db()

        self.assertEqual(order.payment_status, "partial")
        self.assertEqual(order.amount_paid, total)

        # Une sauvegarde ordinaire ne doit surtout pas fabriquer PAID.
        order.save()
        order.refresh_from_db()

        self.assertEqual(order.payment_status, "partial")
        self.assertEqual(order.amount_paid, total)

        self.assertFalse(
            Payment.objects.filter(order=order).exists()
        )

        # Le recalcul canonique depuis Payment corrige ensuite
        # naturellement cet état incohérent.
        order.sync_payment_status_from_payments(save=True)
        order.refresh_from_db()

        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(order.amount_paid, Decimal("0"))
