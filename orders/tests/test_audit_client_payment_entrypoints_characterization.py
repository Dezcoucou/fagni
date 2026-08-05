"""
Audit parcours client E2E V1 - tests de caracterisation, aucune correction
de production. Couvre les points d'entree de paiement cote client :
client_order_pay_simulate, client_order_pay_cash (orders/views.py).

Constats verifies directement en lisant le code avant d'ecrire ces tests :
- client_order_pay_simulate n'a AUCUNE verification de settings.DEBUG : en
  production (DEBUG=False), un client authentifie peut s'auto-confirmer
  un paiement complet sans passer par aucun moyen de paiement reel ;
- build_order_canonical_snapshot calcule
  "can_pay": (payment_status_canonical != "paid" and total_client_ttc > 0)
  SANS jamais verifier order.status == "canceled" : une commande annulee
  reste "payable" pour simulate ET cash ;
- client_order_pay_cash utilise reference=f"API-{order.id}" (FIXE, non
  liee au montant ni a un timestamp) pour CHAQUE acompte cash. Or
  apply_order_payment fait un lookup par (order, reference) pour
  l'idempotence : un 2e acompte legitime avec un montant DIFFERENT lève
  une ValidationError non rattrapee (500) ; un 2e acompte du MEME montant
  est traite comme un simple replay idempotent (aucun montant supplementaire
  applique) ;
- l'ownership (filter customer__phone=phone) est deja correcte sur les deux
  vues - caracterisee ici comme deja-bonne, pas comme un bug ;
- le guard "deja solde" (paid_now >= total -> 409) est deja present sur les
  deux vues AVANT apply_order_payment - deja-bon egalement ;
- apply_order_payment refuse deja, plus bas niveau, tout paiement sur une
  commande canceled (ValidationError "Impossible d'enregistrer un paiement
  sur une commande annulée.") - la protection des DONNEES est donc deja
  correcte (amount_paid reste a 0). Mais ni client_order_pay_simulate ni
  client_order_pay_cash ne rattrapent cette ValidationError : la reponse
  HTTP est un crash 500 (Client(raise_request_exception=False) utilise
  ci-dessous pour l'observer proprement), pas un rejet propre (4xx) comme
  attendu cote client.

Ces tests expriment le comportement METIER SECURISE attendu et echouent
donc majoritairement avant correction - c'est le resultat attendu de cette
etape de caracterisation.
"""
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from orders.models import Customer, Order, Payment

CLIENT_PHONE_COOKIE = "client_phone"


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_order(customer, total=Decimal("10000"), status="in_progress",
                 payment_status="unpaid", amount_paid=Decimal("0"), is_draft=False):
    return Order.objects.create(
        customer=customer,
        status=status,
        payment_status=payment_status,
        amount_paid=amount_paid,
        total_client_ttc=total,
        is_draft=is_draft,
    )


def _client_with_phone(phone):
    c = Client()
    c.cookies[CLIENT_PHONE_COOKIE] = phone
    return c


def _pay_simulate(client, order):
    return client.post(reverse("orders:client_order_pay_simulate", args=[order.id]))


def _pay_cash(client, order, amount, note=""):
    return client.post(
        reverse("orders:client_order_pay_cash", args=[order.id]),
        data={"amount": str(amount), "note": note},
    )


class ClientOrderPaySimulateProductionGuardTests(TestCase):
    @override_settings(DEBUG=False)
    def test_simulate_refused_when_debug_false(self):
        customer = _make_customer("0700070001")
        order = _make_order(customer)
        client = _client_with_phone("0700070001")

        resp = _pay_simulate(client, order)

        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 200,
            "client_order_pay_simulate doit etre refuse en production (DEBUG=False)",
        )
        self.assertNotEqual(order.payment_status, "paid")
        self.assertEqual(order.amount_paid, Decimal("0"))

    @override_settings(DEBUG=True)
    def test_simulate_allowed_when_debug_true(self):
        """Caracterise le comportement MVP actuel en environnement de dev,
        pour bien isoler que le probleme est specifique a DEBUG=False."""
        customer = _make_customer("0700070002")
        order = _make_order(customer)
        client = _client_with_phone("0700070002")

        resp = _pay_simulate(client, order)

        order.refresh_from_db()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(order.payment_status, "paid")


class ClientCannotAutoConfirmCashTests(TestCase):
    @override_settings(DEBUG=True)
    def test_client_cannot_auto_confirm_cash_without_real_payment(self):
        """Le client ne doit jamais pouvoir se déclarer lui-même 'payé cash'
        sans verification tierce (livreur/OPS). Cible: la route ne doit
        jamais créer directement un Payment confirmé sans validation
        externe. Constat actuel : apply_order_payment cree un Payment
        confirme immediatement sur simple declaration client."""
        customer = _make_customer("0700070003")
        order = _make_order(customer)
        client = _client_with_phone("0700070003")

        resp = _pay_cash(client, order, "10000")

        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 200,
            "un client ne doit pas pouvoir auto-confirmer un paiement cash sans verification tierce",
        )
        self.assertNotEqual(order.payment_status, "paid")


class CanceledOrderPaymentEntrypointsTests(TestCase):
    def _client_with_phone_non_raising(self, phone):
        c = Client(raise_request_exception=False)
        c.cookies[CLIENT_PHONE_COOKIE] = phone
        return c

    @override_settings(DEBUG=True)
    def test_canceled_order_refuses_simulate(self):
        customer = _make_customer("0700070004")
        order = _make_order(customer, status="canceled")
        client = self._client_with_phone_non_raising("0700070004")

        resp = _pay_simulate(client, order)

        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 500,
            "une commande annulee doit refuser simulate proprement (4xx), pas planter (500)",
        )
        self.assertEqual(order.amount_paid, Decimal("0"), "aucun paiement ne doit etre applique (deja correct)")
        self.assertNotEqual(order.payment_status, "paid")

    @override_settings(DEBUG=True)
    def test_canceled_order_refuses_cash(self):
        customer = _make_customer("0700070005")
        order = _make_order(customer, status="canceled")
        client = self._client_with_phone_non_raising("0700070005")

        resp = _pay_cash(client, order, "5000")

        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 500,
            "une commande annulee doit refuser cash proprement (4xx), pas planter (500)",
        )
        self.assertEqual(order.amount_paid, Decimal("0"), "aucun paiement ne doit etre applique (deja correct)")


class AlreadySettledOrderRefusesNewWritesTests(TestCase):
    @override_settings(DEBUG=True)
    def test_already_paid_order_refuses_new_simulate(self):
        customer = _make_customer("0700070006")
        order = _make_order(customer, payment_status="paid", amount_paid=Decimal("10000"))
        client = _client_with_phone("0700070006")

        resp = _pay_simulate(client, order)

        self.assertEqual(resp.status_code, 409)
        order.refresh_from_db()
        self.assertEqual(order.amount_paid, Decimal("10000"))

    @override_settings(DEBUG=True)
    def test_already_paid_order_refuses_new_cash(self):
        customer = _make_customer("0700070007")
        order = _make_order(customer, payment_status="paid", amount_paid=Decimal("10000"))
        client = _client_with_phone("0700070007")

        resp = _pay_cash(client, order, "1000")

        self.assertEqual(resp.status_code, 409)
        order.refresh_from_db()
        self.assertEqual(order.amount_paid, Decimal("10000"))


class ClientCannotPayForeignOrderTests(TestCase):
    @override_settings(DEBUG=True)
    def test_client_cannot_pay_another_clients_order(self):
        owner = _make_customer("0700070008")
        stranger_phone = "0700070009"
        _make_customer(stranger_phone)
        order = _make_order(owner)
        stranger_client = _client_with_phone(stranger_phone)

        resp = _pay_simulate(stranger_client, order)

        order.refresh_from_db()
        self.assertEqual(resp.status_code, 404, "un client ne doit pas trouver la commande d'un autre client")
        self.assertEqual(order.amount_paid, Decimal("0"))


class CashReferenceCollisionTests(TestCase):
    @override_settings(DEBUG=True)
    def test_two_legitimate_cash_installments_different_amounts_do_not_conflict(self):
        """Deux acomptes cash legitimes et distincts (montants differents)
        doivent tous les deux s'appliquer. Constat actuel : la reference
        fixe API-{order.id} fait que le 2e acompte est traite comme une
        relecture du 1er par apply_order_payment (montant different =>
        ValidationError non rattrapee -> 500)."""
        customer = _make_customer("0700070010")
        order = _make_order(customer, total=Decimal("20000"))
        client = _client_with_phone("0700070010")

        resp1 = _pay_cash(client, order, "5000")
        self.assertEqual(resp1.status_code, 200)

        resp2 = _pay_cash(client, order, "7000")

        order.refresh_from_db()
        self.assertEqual(
            resp2.status_code, 200,
            "un 2e acompte cash legitime avec un montant different ne doit jamais planter (500)",
        )
        self.assertEqual(
            order.amount_paid, Decimal("12000"),
            "les deux acomptes cash distincts doivent s'additionner (5000 + 7000)",
        )

    @override_settings(DEBUG=True)
    def test_two_legitimate_cash_installments_same_amount_both_apply(self):
        """Deux acomptes cash legitimes du MEME montant (coincidence
        plausible) doivent aussi s'additionner tous les deux. Constat
        actuel : le 2e est traite comme un replay idempotent du 1er
        (meme reference + meme montant) et n'ajoute rien."""
        customer = _make_customer("0700070011")
        order = _make_order(customer, total=Decimal("20000"))
        client = _client_with_phone("0700070011")

        _pay_cash(client, order, "5000")
        _pay_cash(client, order, "5000")

        order.refresh_from_db()
        self.assertEqual(
            order.amount_paid, Decimal("10000"),
            "deux acomptes cash distincts du meme montant doivent tous les deux etre comptabilises",
        )
        self.assertEqual(
            Payment.objects.filter(order=order).count(), 2,
            "chaque acompte cash legitime doit creer sa propre ligne Payment",
        )


class CashPaymentReferenceUniquenessAndIdempotencyTests(TestCase):
    @override_settings(DEBUG=True)
    def test_distinct_legitimate_installments_should_have_distinct_references(self):
        """Deux acomptes cash legitimes et distincts doivent produire deux
        lignes Payment avec des references DISTINCTES (tracables
        separement). Constat actuel : reference=f"API-{order.id}" est fixe
        par commande, jamais par transaction -> impossible de distinguer
        deux acomptes distincts d'une simple relecture."""
        customer = _make_customer("0700070013")
        order = _make_order(customer, total=Decimal("20000"))
        client = Client(raise_request_exception=False)
        client.cookies[CLIENT_PHONE_COOKIE] = "0700070013"

        _pay_cash(client, order, "5000")
        _pay_cash(client, order, "7000")

        payments = list(Payment.objects.filter(order=order).values_list("reference", flat=True))
        self.assertEqual(
            len(payments), 2,
            "deux acomptes cash distincts et legitimes doivent produire deux lignes Payment",
        )
        self.assertEqual(
            len(payments), len(set(payments)),
            "chaque acompte cash distinct doit avoir sa propre reference, jamais une reference partagee",
        )

    @override_settings(DEBUG=True)
    def test_apply_order_payment_exact_repetition_is_idempotent(self):
        """Propriete deja-correcte de l'orchestrateur apply_order_payment
        lui-meme (independamment du bug de reference fixe de la vue) : un
        rejeu EXACT (meme reference, meme montant - ex. double-clic ou
        retry reseau) ne doit jamais dupliquer le paiement."""
        from orders.views import apply_order_payment

        customer = _make_customer("0700070014")
        order = _make_order(customer, total=Decimal("20000"))

        apply_order_payment(order, Decimal("5000"), channel="api", reference="RETRY-TOKEN-1", note="")
        apply_order_payment(order, Decimal("5000"), channel="api", reference="RETRY-TOKEN-1", note="")

        order.refresh_from_db()
        self.assertEqual(
            Payment.objects.filter(order=order, reference="RETRY-TOKEN-1").count(), 1,
            "un rejeu exact de la meme reference ne doit jamais creer un 2e Payment",
        )
        self.assertEqual(order.amount_paid, Decimal("5000"), "le rejeu exact ne doit pas doubler le montant applique")


class NoUnverifiedRouteCreatesConfirmedPaymentTests(TestCase):
    @override_settings(DEBUG=True)
    def test_cash_payment_is_marked_as_client_declared_not_third_party_confirmed(self):
        """Aucune route client non verifiee ne doit creer directement un
        Payment confirme par un tiers : un Payment issu de cette route doit
        etre tracable comme 'declare par le client', pas 'confirme'."""
        customer = _make_customer("0700070012")
        order = _make_order(customer, total=Decimal("10000"))
        client = _client_with_phone("0700070012")

        _pay_cash(client, order, "10000")

        payment = Payment.objects.filter(order=order).order_by("-id").first()
        self.assertIsNotNone(payment)
        self.assertIsNone(
            payment.confirmed_by_id,
            "un paiement declare par le client via une route non verifiee ne doit jamais porter confirmed_by",
        )
