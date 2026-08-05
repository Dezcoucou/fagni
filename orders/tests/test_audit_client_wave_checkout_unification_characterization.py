"""
Audit parcours client E2E V1 - tests de caracterisation, aucune correction
de production. Couvre la duplication de l'integration Wave Checkout entre
la page HTML (orders/views.py::client_order_pay_wave_page) et la route DRF
(orders/client_api.py::_get_or_create_wave_checkout).

Constats verifies directement en lisant le code avant d'ecrire ces tests :
- client_order_pay_wave_page appelle l'API Wave (urllib.request.urlopen)
  A CHAQUE GET, sans jamais lire/reutiliser order.wave_checkout_id ni
  respecter le TTL de 25 min (WAVE_CHECKOUT_SESSION_TTL_SECONDS) - contrairement
  a _get_or_create_wave_checkout qui, lui, verrouille la ligne (select_for_update)
  et reutilise la session existante si elle a moins de 25 min ;
- client_order_pay_wave_page ecrit le checkout_id obtenu dans
  order.payment_declared_reference (PAS dans order.wave_checkout_id/url),
  exactement l'anti-pattern que le docstring de _get_or_create_wave_checkout
  decrit explicitement comme "l'ancien id de session Checkout de
  client_order_pay_wave_page" a ne jamais reproduire ;
- consequence directe : une reference declaree manuellement par le client
  (POST action=declare_wave_paid) dans payment_declared_reference est
  ensuite ECRASEE au prochain simple rechargement (GET) de la meme page,
  puisque le bloc d'appel Wave s'execute a chaque GET et reecrit ce champ ;
- aucune verification de order.status=="canceled" n'existe avant de lancer
  un nouvel appel Wave sur cette page (seul remain<=0 bloque implicitement
  les commandes deja entierement payees, via amount_xof="0").

Ces tests mockent integralement urllib.request.urlopen (aucun appel reseau
reel, aucun parametre Wave reel touche) et expriment le comportement METIER
CIBLE (session unique partagee, champ dedie, TTL respecte) - ils echouent
donc majoritairement avant correction.
"""
import json
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from orders.models import Customer, Order

CLIENT_PHONE_COOKIE = "client_phone"

WAVE_SETTINGS = dict(
    WAVE_CHECKOUT_ENABLED=True,
    WAVE_CHECKOUT_API_KEY="test_wave_api_key",
    WAVE_CHECKOUT_SIGNING_SECRET="",
    SITE_BASE_URL="http://testserver",
)


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_order(customer, total=Decimal("10000"), status="in_progress",
                 payment_status="unpaid", amount_paid=Decimal("0")):
    return Order.objects.create(
        customer=customer,
        status=status,
        payment_status=payment_status,
        amount_paid=amount_paid,
        total_client_ttc=total,
        is_draft=False,
    )


def _client_with_phone(phone):
    c = Client()
    c.cookies[CLIENT_PHONE_COOKIE] = phone
    return c


def _mock_urlopen_cm(checkout_id="checkout_test_abc", url="https://checkout.wave.com/xyz"):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps({"id": checkout_id, "wave_launch_url": url}).encode("utf-8")
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value = mock_resp
    mock_cm.__exit__.return_value = False
    return mock_cm


def _requested_amount(mock_urlopen, call_index=0):
    req = mock_urlopen.call_args_list[call_index][0][0]
    body = json.loads(req.data.decode("utf-8"))
    return Decimal(str(body.get("amount")))


class HtmlPageReusesSameCheckoutAcrossGetsTests(TestCase):
    @override_settings(**WAVE_SETTINGS)
    def test_two_successive_gets_reuse_the_same_checkout(self):
        customer = _make_customer("0700090001")
        order = _make_order(customer)
        client = _client_with_phone("0700090001")

        with patch("urllib.request.urlopen", return_value=_mock_urlopen_cm()) as mocked:
            client.get(reverse("orders:client_order_pay_wave_page", args=[order.id]))
            client.get(reverse("orders:client_order_pay_wave_page", args=[order.id]))

        self.assertEqual(
            mocked.call_count, 1,
            "deux GET successifs doivent reutiliser la meme session Wave, pas en creer une seconde",
        )


class HtmlPageStoresSessionInDedicatedFieldTests(TestCase):
    @override_settings(**WAVE_SETTINGS)
    def test_html_page_stores_checkout_in_wave_checkout_id_field(self):
        customer = _make_customer("0700090002")
        order = _make_order(customer)
        client = _client_with_phone("0700090002")

        with patch("urllib.request.urlopen", return_value=_mock_urlopen_cm(checkout_id="checkout_test_html")):
            client.get(reverse("orders:client_order_pay_wave_page", args=[order.id]))

        order.refresh_from_db()
        self.assertEqual(
            order.wave_checkout_id, "checkout_test_html",
            "la session Wave creee par la page HTML doit etre stockee dans wave_checkout_id",
        )
        self.assertTrue(
            order.wave_checkout_url,
            "l'URL de la session Wave doit egalement etre stockee dans wave_checkout_url",
        )


class ManualPaymentReferenceNeverOverwrittenTests(TestCase):
    @override_settings(**WAVE_SETTINGS)
    def test_manual_declared_reference_survives_a_page_reload(self):
        customer = _make_customer("0700090003")
        order = _make_order(customer)
        client = _client_with_phone("0700090003")

        proof = SimpleUploadedFile("preuve.jpg", b"fake-image-bytes", content_type="image/jpeg")
        with patch("urllib.request.urlopen", return_value=_mock_urlopen_cm()):
            client.post(
                reverse("orders:client_order_pay_wave_page", args=[order.id]),
                data={"action": "declare_wave_paid", "payment_reference": "MANUAL-REF-001", "payment_proof": proof},
            )

        order.refresh_from_db()
        self.assertEqual(order.payment_declared_reference, "MANUAL-REF-001")

        with patch("urllib.request.urlopen", return_value=_mock_urlopen_cm(checkout_id="checkout_test_overwrite")):
            client.get(reverse("orders:client_order_pay_wave_page", args=[order.id]))

        order.refresh_from_db()
        self.assertEqual(
            order.payment_declared_reference, "MANUAL-REF-001",
            "une reference declaree manuellement par le client ne doit jamais etre ecrasee par un simple rechargement de page",
        )


class HtmlAndDrfPathsShareTheSameSessionTests(TestCase):
    @override_settings(**WAVE_SETTINGS)
    def test_html_page_and_drf_endpoint_share_the_same_checkout_session(self):
        customer = _make_customer("0700090004")
        order = _make_order(customer)
        client = _client_with_phone("0700090004")

        import jwt
        from django.conf import settings as dj_settings
        token = jwt.encode({"cid": customer.id, "phone": customer.phone}, dj_settings.SECRET_KEY, algorithm="HS256")

        with patch("urllib.request.urlopen", return_value=_mock_urlopen_cm()) as mocked:
            client.get(reverse("orders:client_order_pay_wave_page", args=[order.id]))
            client.get(
                reverse("api-client-order-detail", args=[order.id]),
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )

        self.assertEqual(
            mocked.call_count, 1,
            "le parcours HTML et l'API client doivent partager la meme session Wave, pas en creer une chacun",
        )


class CheckoutRefusedOnInvalidOrderStateTests(TestCase):
    @override_settings(**WAVE_SETTINGS)
    def test_checkout_refused_on_canceled_order(self):
        customer = _make_customer("0700090005")
        order = _make_order(customer, status="canceled")
        client = _client_with_phone("0700090005")

        with patch("urllib.request.urlopen", return_value=_mock_urlopen_cm()) as mocked:
            client.get(reverse("orders:client_order_pay_wave_page", args=[order.id]))

        self.assertEqual(mocked.call_count, 0, "aucune session Wave ne doit etre creee pour une commande annulee")

    @override_settings(**WAVE_SETTINGS)
    def test_checkout_refused_on_already_paid_order(self):
        customer = _make_customer("0700090006")
        order = _make_order(customer, payment_status="paid", amount_paid=Decimal("10000"))
        client = _client_with_phone("0700090006")

        with patch("urllib.request.urlopen", return_value=_mock_urlopen_cm()) as mocked:
            client.get(reverse("orders:client_order_pay_wave_page", args=[order.id]))

        self.assertEqual(mocked.call_count, 0, "aucune session Wave ne doit etre creee pour une commande deja payee")


class AmountSentMatchesLockedRemainingTests(TestCase):
    @override_settings(**WAVE_SETTINGS)
    def test_amount_sent_to_wave_matches_locked_remaining_amount(self):
        customer = _make_customer("0700090007")
        order = _make_order(customer, total=Decimal("12345"), payment_status="partial", amount_paid=Decimal("2345"))
        client = _client_with_phone("0700090007")

        with patch("urllib.request.urlopen", return_value=_mock_urlopen_cm()) as mocked:
            client.get(reverse("orders:client_order_pay_wave_page", args=[order.id]))

        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(_requested_amount(mocked), Decimal("10000"))


class NoSecondSessionWithinTtlTests(TestCase):
    @override_settings(**WAVE_SETTINGS)
    def test_get_or_create_wave_checkout_does_not_create_second_session_within_ttl(self):
        """Caracterise le comportement DEJA CORRECT de
        _get_or_create_wave_checkout (route DRF) : deux appels rapproches
        dans le TTL de 25 min ne doivent creer qu'une seule session."""
        customer = _make_customer("0700090008")
        order = _make_order(customer)

        import jwt
        from django.conf import settings as dj_settings
        token = jwt.encode({"cid": customer.id, "phone": customer.phone}, dj_settings.SECRET_KEY, algorithm="HS256")
        client = Client()

        with patch("urllib.request.urlopen", return_value=_mock_urlopen_cm()) as mocked:
            client.get(reverse("api-client-order-detail", args=[order.id]), HTTP_AUTHORIZATION=f"Bearer {token}")
            client.get(reverse("api-client-order-detail", args=[order.id]), HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(
            mocked.call_count, 1,
            "aucune seconde session Wave ne doit etre creee pendant le TTL de 25 minutes",
        )
