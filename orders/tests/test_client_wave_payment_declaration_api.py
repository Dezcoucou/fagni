from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from orders.client_api import _make_token
from orders.models import Customer, Order, Payment


class ClientWavePaymentDeclarationApiTests(TestCase):
    def test_declaration_sans_authentification_est_refusee(self):
        response = self.client.post(
            "/api/client/orders/999/payment/declare-wave/",
            data={
                "payment_reference": "WAVE-ANONYME-001",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json()["error"],
            "Authentification requise.",
        )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_tmp = TemporaryDirectory()
        cls._media_override = override_settings(
            MEDIA_ROOT=Path(cls._media_tmp.name)
        )
        cls._media_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._media_override.disable()
        cls._media_tmp.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client Wave Declaration",
            phone="0700778801",
            address="Riviera 3",
        )

        self.other_customer = Customer.objects.create(
            name="Autre Client",
            phone="0700778802",
            address="Riviera 3",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status="pending",
            payment_status="pending",
            pricing_mode="item",
            total_client_ttc=Decimal("5000"),
            amount_paid=Decimal("0"),
        )

        self.headers = {
            "HTTP_AUTHORIZATION":
            f"Bearer {_make_token(self.customer)}"
        }

        self.other_headers = {
            "HTTP_AUTHORIZATION":
            f"Bearer {_make_token(self.other_customer)}"
        }

    def url(self, order=None):
        order = order or self.order
        return reverse(
            "api-client-declare-wave-payment",
            args=[order.id],
        )

    def proof(self, name="preuve.png"):
        return SimpleUploadedFile(
            name,
            b"fake-image-content",
            content_type="image/png",
        )

    def declare(self, *, reference="WAVE-REF-001", proof=None):
        data = {
            "payment_reference": reference,
        }

        if proof is not False:
            data["payment_proof"] = proof or self.proof()

        return self.client.post(
            self.url(),
            data=data,
            **self.headers,
        )

    def test_client_proprietaire_peut_declarer_paiement(self):
        response = self.declare()

        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.payment_status,
            "declared",
        )
        self.assertEqual(
            self.order.payment_verification_status,
            "pending_review",
        )
        self.assertEqual(
            self.order.payment_declared_channel,
            "wave",
        )
        self.assertEqual(
            self.order.payment_declared_reference,
            "WAVE-REF-001",
        )
        self.assertIsNotNone(
            self.order.payment_declared_at
        )
        self.assertTrue(
            bool(self.order.payment_proof)
        )

    def test_declaration_ne_cree_aucun_payment(self):
        amount_before = self.order.amount_paid

        response = self.declare()

        self.assertEqual(response.status_code, 200)

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.amount_paid,
            amount_before,
        )
        self.assertFalse(
            Payment.objects.filter(
                order=self.order
            ).exists()
        )

    def test_autre_client_ne_peut_pas_declarer(self):
        response = self.client.post(
            self.url(),
            data={
                "payment_reference": "REF-INTERDITE",
                "payment_proof": self.proof(),
            },
            **self.other_headers,
        )

        self.assertEqual(response.status_code, 404)

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.payment_status,
            "pending",
        )
        self.assertFalse(
            Payment.objects.filter(
                order=self.order
            ).exists()
        )

    def test_commande_annulee_est_refusee(self):
        Order.objects.filter(
            pk=self.order.pk
        ).update(status="canceled")

        self.order.refresh_from_db()

        response = self.declare()

        self.assertEqual(response.status_code, 400)

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.payment_status,
            "pending",
        )
        self.assertFalse(
            Payment.objects.filter(
                order=self.order
            ).exists()
        )

    def test_commande_deja_payee_est_refusee(self):
        Order.objects.filter(
            pk=self.order.pk
        ).update(
            payment_status="paid",
            amount_paid=Decimal("5000"),
        )

        self.order.refresh_from_db()

        response = self.declare()

        self.assertEqual(response.status_code, 400)

        self.assertFalse(
            Payment.objects.filter(
                order=self.order
            ).exists()
        )

    def test_reference_est_obligatoire(self):
        response = self.client.post(
            self.url(),
            data={
                "payment_reference": "",
                "payment_proof": self.proof(),
            },
            **self.headers,
        )

        self.assertEqual(response.status_code, 400)

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.payment_status,
            "pending",
        )

    def test_preuve_est_obligatoire_premiere_fois(self):
        response = self.declare(
            proof=False
        )

        self.assertEqual(response.status_code, 400)

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.payment_status,
            "pending",
        )

    def test_resoumission_ne_cree_pas_de_payment(self):
        first = self.declare(
            reference="WAVE-REF-FIRST",
        )

        self.assertEqual(first.status_code, 200)

        second = self.declare(
            reference="WAVE-REF-SECOND",
            proof=self.proof("preuve-2.png"),
        )

        self.assertEqual(second.status_code, 200)

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.payment_status,
            "declared",
        )
        self.assertEqual(
            self.order.payment_verification_status,
            "pending_review",
        )
        self.assertEqual(
            self.order.payment_declared_reference,
            "WAVE-REF-SECOND",
        )
        self.assertEqual(
            self.order.amount_paid,
            Decimal("0"),
        )
        self.assertEqual(
            Payment.objects.filter(
                order=self.order
            ).count(),
            0,
        )

    def test_detail_expose_etat_de_verification(self):
        declaration = self.declare()

        self.assertEqual(
            declaration.status_code,
            200,
        )

        response = self.client.get(
            reverse(
                "api-client-order-detail",
                args=[self.order.id],
            ),
            **self.headers,
        )

        self.assertEqual(response.status_code, 200)

        body = response.json()

        self.assertEqual(
            body["payment_status"],
            "declared",
        )
        self.assertEqual(
            body["payment_verification_status"],
            "pending_review",
        )
        self.assertTrue(
            body["has_payment_proof"]
        )
        self.assertIsNotNone(
            body["payment_declared_at"]
        )
