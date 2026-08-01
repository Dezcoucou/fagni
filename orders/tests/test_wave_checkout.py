"""
Sprint P0, Wave 2 (BP1) - auto-confirmation des paiements Wave.

Le webhook Wave (signature HMAC, anti-rejeu, idempotence, apply_order_payment)
existait deja et n'est pas modifie ici, sauf pour son rattachement commande
<-> checkout_id (lecture du nouveau champ dedie wave_checkout_id, avec repli
retrocompatible sur payment_declared_reference). Le vrai changement de cette
Wave est cote client_api.py : creation/reutilisation d'une session Checkout
Wave quand WAVE_CHECKOUT_ENABLED=true, avec fallback sur le lien marchand
statique existant si le flag est desactive ou si Wave echoue/timeout.
"""
import hashlib
import hmac
import json
import threading
import time
from decimal import Decimal
from unittest.mock import patch

import jwt
from django.conf import settings
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from orders.client_api import WAVE_CHECKOUT_SESSION_TTL_SECONDS, _get_or_create_wave_checkout, _make_token
from orders.models import Customer, Order, WaveEvent


def _make_order(total, amount_paid="0", phone="0700001111", **extra):
    customer, _ = Customer.objects.get_or_create(
        phone=phone, defaults={"name": "Client Test", "address": "Riviera 3"},
    )
    return Order.objects.create(
        customer=customer,
        pricing_mode="item",
        total_client_ttc=Decimal(str(total)),
        amount_paid=Decimal(str(amount_paid)),
        **extra,
    )


def _client_headers(customer):
    return {"HTTP_AUTHORIZATION": f"Bearer {_make_token(customer)}"}


def _token_ops():
    return jwt.encode({"ops": True, "name": "Test OPS"}, settings.SECRET_KEY, algorithm="HS256")


def _ops_headers():
    return {"HTTP_AUTHORIZATION": f"Bearer {_token_ops()}"}


class _FakeWaveResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _fake_session_creation(payload_by_call=None, error=None):
    """Fabrique un side_effect pour urllib.request.urlopen simulant la
    creation de session Checkout Wave (POST /v1/checkout/sessions)."""
    calls = {"n": 0}

    def _urlopen(req, timeout=8):
        calls["n"] += 1
        if error is not None:
            raise error
        payload = payload_by_call or {"id": "cs_test_1", "wave_launch_url": "https://checkout.wave.com/cs_test_1"}
        return _FakeWaveResponse(payload)

    _urlopen.calls = calls
    return _urlopen


class _FakeRequest:
    def build_absolute_uri(self, path):
        return "http://testserver" + path


class WaveCheckoutFlagDisabledTests(TestCase):
    """Flag off : comportement strictement inchangue (lien statique, aucun appel Wave)."""

    def test_flag_desactive_lien_statique_inchange(self):
        order = _make_order(total="5000")

        with patch("urllib.request.urlopen") as mocked:
            resp = self.client.get(
                reverse("api-client-order-detail", args=[order.id]),
                **_client_headers(order.customer),
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["wave_link"].startswith("https://pay.wave.com/m/"))
        mocked.assert_not_called()

        order.refresh_from_db()
        self.assertEqual(order.wave_checkout_id, "")
        self.assertEqual(order.wave_checkout_url, "")


@override_settings(WAVE_CHECKOUT_ENABLED=True, WAVE_CHECKOUT_API_KEY="test-key")
class WaveCheckoutFlagEnabledTests(TestCase):
    """Flag on : session Checkout creee, reutilisee, ou repli propre selon les cas."""

    def test_flag_active_retourne_url_session_checkout(self):
        order = _make_order(total="5000")
        fake = _fake_session_creation()

        with patch("urllib.request.urlopen", side_effect=fake):
            resp = self.client.get(
                reverse("api-client-order-detail", args=[order.id]),
                **_client_headers(order.customer),
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["wave_link"], "https://checkout.wave.com/cs_test_1")
        self.assertEqual(fake.calls["n"], 1)

        order.refresh_from_db()
        self.assertEqual(order.wave_checkout_id, "cs_test_1")
        self.assertEqual(order.wave_checkout_url, "https://checkout.wave.com/cs_test_1")
        self.assertIsNotNone(order.wave_checkout_created_at)

    def test_echec_wave_retombe_sur_lien_statique_sans_bloquer(self):
        order = _make_order(total="5000")
        fake = _fake_session_creation(error=TimeoutError("wave down"))

        with patch("urllib.request.urlopen", side_effect=fake):
            resp = self.client.get(
                reverse("api-client-order-detail", args=[order.id]),
                **_client_headers(order.customer),
            )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["wave_link"].startswith("https://pay.wave.com/m/"))

        order.refresh_from_db()
        self.assertEqual(order.wave_checkout_id, "")

    def test_appels_repetes_une_seule_session(self):
        order = _make_order(total="5000")
        fake = _fake_session_creation()

        with patch("urllib.request.urlopen", side_effect=fake):
            resp1 = self.client.get(
                reverse("api-client-order-detail", args=[order.id]),
                **_client_headers(order.customer),
            )
            resp2 = self.client.get(
                reverse("api-client-order-detail", args=[order.id]),
                **_client_headers(order.customer),
            )

        self.assertEqual(fake.calls["n"], 1)
        self.assertEqual(resp1.json()["wave_link"], resp2.json()["wave_link"])

    def test_session_expiree_est_renouvelee(self):
        order = _make_order(total="5000")
        order.wave_checkout_id = "cs_old"
        order.wave_checkout_url = "https://checkout.wave.com/cs_old"
        order.wave_checkout_created_at = timezone.now() - timezone.timedelta(
            seconds=WAVE_CHECKOUT_SESSION_TTL_SECONDS + 1
        )
        order.save(update_fields=["wave_checkout_id", "wave_checkout_url", "wave_checkout_created_at"])

        fake = _fake_session_creation({"id": "cs_new", "wave_launch_url": "https://checkout.wave.com/cs_new"})

        with patch("urllib.request.urlopen", side_effect=fake):
            checkout_url, checkout_id = _get_or_create_wave_checkout(order, 5000, _FakeRequest())

        self.assertEqual(fake.calls["n"], 1)
        self.assertEqual(checkout_id, "cs_new")
        order.refresh_from_db()
        self.assertEqual(order.wave_checkout_id, "cs_new")

    def test_reference_declaree_manuellement_jamais_ecrasee(self):
        """
        payment_declared_reference melange deja 3 usages (declaration client,
        reference libre OPS, ancien id de session Checkout) : la creation
        d'une session dediee ne doit jamais y toucher.
        """
        order = _make_order(total="5000")
        order.payment_declared_reference = "REF-SAISIE-PAR-LE-CLIENT"
        order.save(update_fields=["payment_declared_reference"])

        fake = _fake_session_creation()
        with patch("urllib.request.urlopen", side_effect=fake):
            _get_or_create_wave_checkout(order, 5000, _FakeRequest())

        order.refresh_from_db()
        self.assertEqual(order.payment_declared_reference, "REF-SAISIE-PAR-LE-CLIENT")
        self.assertEqual(order.wave_checkout_id, "cs_test_1")


@override_settings(WAVE_CHECKOUT_ENABLED=True, WAVE_CHECKOUT_API_KEY="test-key")
class WaveCheckoutConcurrencyTests(TransactionTestCase):
    """
    Deux requetes simultanees ne doivent jamais provoquer deux sessions
    "reussies" distinctes, ni faire remonter une exception au client.

    Note honnete : la garantie forte (une seule session creee) repose sur
    select_for_update(), deja utilise partout ailleurs dans ce depot pour le
    meme type de probleme (apply_order_payment, wave_webhook) — et qui
    necessite un verrou de ligne reel (Postgres, utilise en production).
    SQLite (utilise ici en local/CI) ne supporte pas ce verrou : ce test
    verifie donc l'invariant qui reste vrai quel que soit le backend —
    jamais de crash, jamais deux ids differents retournes comme "reussis" en
    meme temps — plutot qu'un comptage strict des appels Wave, qui ne serait
    fiable que sur Postgres.
    """

    def test_concurrence_aucune_duplication_ni_exception(self):
        order = _make_order(total="5000", phone="0700002222")
        lock = threading.Lock()
        call_count = {"n": 0}

        def fake_urlopen(req, timeout=8):
            with lock:
                call_count["n"] += 1
                n = call_count["n"]
            time.sleep(0.2)
            return _FakeWaveResponse({"id": f"cs_{n}", "wave_launch_url": f"https://checkout.wave.com/{n}"})

        results = {}
        errors = {}
        barrier = threading.Barrier(2)

        def worker(name):
            connections.close_all()
            barrier.wait()
            try:
                with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                    results[name] = _get_or_create_wave_checkout(order, 5000, _FakeRequest())
            except Exception as exc:  # ne doit jamais arriver
                errors[name] = exc

        t1 = threading.Thread(target=worker, args=("t1",))
        t2 = threading.Thread(target=worker, args=("t2",))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(errors, {}, "aucune exception ne doit remonter au client")

        successful = [v for v in results.values() if v != (None, None)]
        distinct_ids = {cid for (_url, cid) in successful}
        self.assertLessEqual(
            len(distinct_ids), 1,
            "jamais deux sessions differentes rendues comme reussies en meme temps",
        )


class WaveWebhookAttachmentTests(TestCase):
    """Rattachement commande <-> checkout_id : champ dedie, puis repli
    retrocompatible sur payment_declared_reference (sessions legacy creees
    par client_order_pay_wave_page)."""

    def _post_debug_event(self, checkout_id, amount, event_id="evt_1"):
        body = {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "id": checkout_id,
                "payment_status": "succeeded",
                "checkout_status": "complete",
                "currency": "XOF",
                "amount": str(amount),
            },
        }
        return self.client.post(
            reverse("orders:wave_webhook"),
            data=json.dumps(body),
            content_type="application/json",
        )

    @override_settings(DEBUG=True)
    def test_rattachement_via_wave_checkout_id(self):
        order = _make_order(total="5000", phone="0700003333")
        order.wave_checkout_id = "checkout_test_new"
        order.save(update_fields=["wave_checkout_id"])

        resp = self._post_debug_event("checkout_test_new", 5000)

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")

    @override_settings(DEBUG=True)
    def test_rattachement_repli_payment_declared_reference(self):
        """Session legacy (client_order_pay_wave_page) : wave_checkout_id
        vide, id stocke seulement dans payment_declared_reference."""
        order = _make_order(total="5000", phone="0700004444")
        order.payment_declared_reference = "checkout_test_legacy"
        order.save(update_fields=["payment_declared_reference"])

        resp = self._post_debug_event("checkout_test_legacy", 5000)

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")

    @override_settings(DEBUG=True)
    def test_rejeu_idempotent(self):
        order = _make_order(total="5000", phone="0700005555")
        order.wave_checkout_id = "checkout_test_idem"
        order.save(update_fields=["wave_checkout_id"])

        r1 = self._post_debug_event("checkout_test_idem", 5000, event_id="evt_idem")
        order.refresh_from_db()
        paid_after_first = order.amount_paid

        r2 = self._post_debug_event("checkout_test_idem", 5000, event_id="evt_idem")
        order.refresh_from_db()

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json().get("idempotent"))
        self.assertEqual(order.amount_paid, paid_after_first)
        self.assertEqual(WaveEvent.objects.filter(event_id="evt_idem").count(), 1)

    @override_settings(DEBUG=True)
    def test_montant_partiel(self):
        order = _make_order(total="5000", phone="0700006666")
        order.wave_checkout_id = "checkout_test_partiel"
        order.save(update_fields=["wave_checkout_id"])

        resp = self._post_debug_event("checkout_test_partiel", 2000, event_id="evt_partiel")

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "partial")
        self.assertEqual(order.amount_paid, Decimal("2000"))

    @override_settings(DEBUG=True)
    def test_commande_deja_soldee_est_idempotente(self):
        from orders.views import apply_order_payment

        order = _make_order(total="5000", phone="0700007777")
        order.wave_checkout_id = "checkout_test_soldee"
        order.save(update_fields=["wave_checkout_id"])

        apply_order_payment(order, Decimal("5000"), channel="manual")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")

        resp = self._post_debug_event("checkout_test_soldee", 5000, event_id="evt_soldee")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("idempotent"))
        order.refresh_from_db()
        self.assertEqual(order.amount_paid, Decimal("5000"))


class WaveWebhookSignatureTests(TestCase):
    """Signature HMAC deja implementee, non modifiee ici : on prouve juste
    qu'elle continue de fonctionner (valide accepte, invalide rejete) apres
    le changement de rattachement."""

    def _signed_headers(self, secret, body_bytes, timestamp=None):
        timestamp = timestamp or str(int(time.time()))
        sig = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + body_bytes, hashlib.sha256).hexdigest()
        return {"HTTP_WAVE_SIGNATURE": f"t={timestamp},v1={sig}"}

    def _remote_verify_fake(self, remote_payload):
        def _urlopen(req, timeout=8):
            return _FakeWaveResponse(remote_payload)
        return _urlopen

    @override_settings(DEBUG=False, WAVE_WEBHOOK_SIGNING_SECRET="whsec_test", WAVE_CHECKOUT_API_KEY="test-key")
    def test_signature_valide_traite_le_paiement(self):
        order = _make_order(total="5000", phone="0700008888")
        order.wave_checkout_id = "cs_real_1"
        order.save(update_fields=["wave_checkout_id"])

        body = {
            "id": "evt_sig_ok",
            "type": "checkout.session.completed",
            "data": {
                "id": "cs_real_1",
                "payment_status": "succeeded",
                "checkout_status": "complete",
                "currency": "XOF",
                "amount": "5000",
            },
        }
        body_bytes = json.dumps(body).encode("utf-8")
        headers = self._signed_headers("whsec_test", body_bytes)

        remote_payload = {
            "id": "cs_real_1",
            "payment_status": "succeeded",
            "checkout_status": "complete",
            "currency": "XOF",
            "amount": "5000",
        }

        with patch("urllib.request.urlopen", side_effect=self._remote_verify_fake(remote_payload)):
            resp = self.client.post(
                reverse("orders:wave_webhook"),
                data=body_bytes,
                content_type="application/json",
                **headers,
            )

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")

    @override_settings(DEBUG=False, WAVE_WEBHOOK_SIGNING_SECRET="whsec_test", WAVE_CHECKOUT_API_KEY="test-key")
    def test_signature_invalide_rejetee(self):
        order = _make_order(total="5000", phone="0700009991")
        order.wave_checkout_id = "cs_real_2"
        order.save(update_fields=["wave_checkout_id"])

        body = {
            "id": "evt_sig_bad",
            "type": "checkout.session.completed",
            "data": {
                "id": "cs_real_2",
                "payment_status": "succeeded",
                "checkout_status": "complete",
                "currency": "XOF",
                "amount": "5000",
            },
        }
        body_bytes = json.dumps(body).encode("utf-8")
        headers = {"HTTP_WAVE_SIGNATURE": "t=1,v1=0000invalide"}

        resp = self.client.post(
            reverse("orders:wave_webhook"),
            data=body_bytes,
            content_type="application/json",
            **headers,
        )

        self.assertEqual(resp.status_code, 401)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "pending")


class OpsMarkPaidUnchangedTests(TestCase):
    """ops_mark_paid n'est touche par aucun changement de cette Wave :
    non-regression simple."""

    def test_ops_mark_paid_toujours_fonctionnel(self):
        order = _make_order(total="5000", phone="0700009999", status="done")

        resp = self.client.post(
            reverse("api-ops-mark-paid", args=[order.id]),
            data=json.dumps({"channel": "wave", "reference": "OPS-REF-1"}),
            content_type="application/json",
            **_ops_headers(),
        )

        self.assertEqual(resp.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
