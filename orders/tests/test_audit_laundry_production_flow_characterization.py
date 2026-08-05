"""
Audit parcours production/blanchisserie V1 - Etape 2 : tests de
caracterisation, aucune correction de production. Couvre
orders:laundry_weighing (action=start) et orders:laundry_weighing_confirm.

Constats verifies directement (shell de test) avant ecriture, hors
perimetre initialement suppose de la mission :
- laundry_weighing (POST) : `return redirect(...) + f"?laundry_id=..."`
  additionne un HttpResponseRedirect et une str -> TypeError, CRASH sur
  TOUT POST (action=start compris) ;
- laundry_weighing_confirm : `HttpResponseRedirect` n'est jamais importe
  dans orders/views.py (seuls HttpResponse/JsonResponse/... le sont) ->
  NameError, CRASH sur le chemin de succes ET sur le chemin de rejet
  (blanchisserie etrangere) ;
- malgre le crash, les ecritures DB qui precedent le `return` s'executent
  bel et bien (wash_complete_time, DeliveryLeg return) - un cas de mutation
  partielle avec reponse HTTP 500 ;
- action=start ecrit order.status="laundry_in_progress" (fantome), jamais
  "in_progress" ;
- ni action=start ni confirm ne verifient qu'une DeliveryLeg pickup est
  "done" avant d'agir ;
- confirm ne positionne jamais Order.status="ready" (aucune ecriture de
  status du tout dans cette fonction) ;
- confirm ne verifie jamais que la commande n'est pas "canceled".

On utilise Client(raise_request_exception=False) pour ces vues connues pour
planter, afin d'obtenir une reponse HTTP exploitable (500) plutot qu'une
ERROR de framework, et pouvoir caracteriser precisement l'etat DB resultant.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from orders.models import Customer, DeliveryLeg, Order
from partners.models import LaundryPartner

User = get_user_model()


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_laundry(phone, email):
    return LaundryPartner.objects.create(name="Pressing Audit", phone=phone, email=email, is_active=True)


def _make_laundry_user(laundry):
    return User.objects.create_user(username=f"laundry_{laundry.id}", email=laundry.email, password="x")


def _make_done_pickup_leg(order):
    """DeliveryLeg.save() force silencieusement une jambe sans driver a
    rester 'pending' si on tente assigned/in_progress/done (garde-fou
    existant, correct). On cree donc 'pending' puis on force 'done' via un
    update() bas niveau qui ne declenche pas ce garde-fou."""
    leg = DeliveryLeg.objects.create(order=order, leg_type="pickup", status="pending")
    DeliveryLeg.objects.filter(pk=leg.pk).update(status="done")
    return leg


def _make_order(phone, laundry, status="in_progress"):
    return Order.objects.create(
        customer=_make_customer(phone),
        laundry_partner=laundry,
        status=status,
        total_client_ttc=Decimal("1000"),
    )


class LaundryWeighingStartActionCharacterizationTests(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def test_action_start_uses_only_in_progress_status(self):
        laundry = _make_laundry("0700020101", "own1@example.com")
        owner_user = _make_laundry_user(laundry)
        order = _make_order("0700020001", laundry)
        DeliveryLeg.objects.create(order=order, leg_type="pickup", status="pending")
        DeliveryLeg.objects.filter(order=order, leg_type="pickup").update(status="done")

        self.client.force_login(owner_user)
        resp = self.client.post(
            reverse("orders:laundry_weighing", args=[order.id]), data={"action": "start"},
        )

        order.refresh_from_db()
        self.assertEqual(
            resp.status_code, 302,
            "la vue plante (TypeError redirect()+str) au lieu de rediriger proprement",
        )
        self.assertEqual(
            order.status, "in_progress",
            "action=start doit utiliser exclusivement Order.status='in_progress', pas un statut fantome",
        )

    def test_action_start_refused_if_pickup_leg_not_done(self):
        laundry = _make_laundry("0700020102", "own2@example.com")
        owner_user = _make_laundry_user(laundry)
        order = _make_order("0700020002", laundry, status="pending")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", status="assigned")

        self.client.force_login(owner_user)
        self.client.post(reverse("orders:laundry_weighing", args=[order.id]), data={"action": "start"})

        order.refresh_from_db()
        self.assertEqual(
            order.status, "pending",
            "action=start doit etre refusee si la jambe pickup n'est pas done",
        )

    def test_foreign_laundry_cannot_start_weighing(self):
        owner = _make_laundry("0700020103", "own3@example.com")
        foreign = _make_laundry("0700020104", "foreign3@example.com")
        foreign_user = _make_laundry_user(foreign)
        order = _make_order("0700020003", owner, status="pending")

        self.client.force_login(foreign_user)
        resp = self.client.post(reverse("orders:laundry_weighing", args=[order.id]), data={"action": "start"})

        order.refresh_from_db()
        self.assertNotEqual(resp.status_code, 500, "le rejet doit etre propre, pas un crash")
        self.assertEqual(order.status, "pending", "aucune modification par une blanchisserie non proprietaire")


class LaundryWeighingConfirmCharacterizationTests(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def test_confirm_refused_if_pickup_leg_not_done(self):
        laundry = _make_laundry("0700020201", "own4@example.com")
        owner_user = _make_laundry_user(laundry)
        order = _make_order("0700020004", laundry, status="in_progress")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", status="in_progress")

        self.client.force_login(owner_user)
        self.client.post(reverse("orders:laundry_weighing_confirm", args=[order.id]))

        order.refresh_from_db()
        self.assertIsNone(
            order.wash_complete_time,
            "confirm ne doit jamais renseigner wash_complete_time si le pickup n'est pas done",
        )
        self.assertFalse(
            DeliveryLeg.objects.filter(order=order, leg_type="return").exists(),
            "aucune jambe return ne doit etre creee si le pickup n'est pas done",
        )

    def test_confirm_success_sets_wash_complete_time(self):
        laundry = _make_laundry("0700020202", "own5@example.com")
        owner_user = _make_laundry_user(laundry)
        order = _make_order("0700020005", laundry, status="in_progress")
        _make_done_pickup_leg(order)

        self.client.force_login(owner_user)
        resp = self.client.post(reverse("orders:laundry_weighing_confirm", args=[order.id]))

        order.refresh_from_db()
        self.assertEqual(resp.status_code, 302, "la vue plante (NameError HttpResponseRedirect) au lieu de rediriger")
        self.assertIsNotNone(order.wash_complete_time)

    def test_confirm_success_sets_order_status_ready(self):
        laundry = _make_laundry("0700020203", "own6@example.com")
        owner_user = _make_laundry_user(laundry)
        order = _make_order("0700020006", laundry, status="in_progress")
        _make_done_pickup_leg(order)

        self.client.force_login(owner_user)
        self.client.post(reverse("orders:laundry_weighing_confirm", args=[order.id]))

        order.refresh_from_db()
        self.assertEqual(
            order.status, "ready",
            "confirm reussi doit positionner Order.status='ready' (jamais implemente dans cette vue)",
        )

    def test_confirm_creates_at_most_one_return_leg(self):
        laundry = _make_laundry("0700020204", "own7@example.com")
        owner_user = _make_laundry_user(laundry)
        order = _make_order("0700020007", laundry, status="in_progress")
        _make_done_pickup_leg(order)

        self.client.force_login(owner_user)
        self.client.post(reverse("orders:laundry_weighing_confirm", args=[order.id]))
        self.client.post(reverse("orders:laundry_weighing_confirm", args=[order.id]))

        self.assertEqual(DeliveryLeg.objects.filter(order=order, leg_type="return").count(), 1)

    def test_confirm_idempotent_on_repetition(self):
        laundry = _make_laundry("0700020205", "own8@example.com")
        owner_user = _make_laundry_user(laundry)
        order = _make_order("0700020008", laundry, status="in_progress")
        _make_done_pickup_leg(order)

        self.client.force_login(owner_user)
        self.client.post(reverse("orders:laundry_weighing_confirm", args=[order.id]))
        order.refresh_from_db()
        first_wash_complete_time = order.wash_complete_time

        self.client.post(reverse("orders:laundry_weighing_confirm", args=[order.id]))
        order.refresh_from_db()

        self.assertEqual(order.wash_complete_time, first_wash_complete_time)

    def test_confirm_canceled_order_cannot_be_marked_ready(self):
        laundry = _make_laundry("0700020206", "own9@example.com")
        owner_user = _make_laundry_user(laundry)
        order = _make_order("0700020009", laundry, status="canceled")

        self.client.force_login(owner_user)
        self.client.post(reverse("orders:laundry_weighing_confirm", args=[order.id]))

        order.refresh_from_db()
        self.assertEqual(order.status, "canceled", "une commande annulee ne doit jamais etre marquee prete")
        self.assertIsNone(
            order.wash_complete_time,
            "aucun effet de bord ne doit avoir lieu sur une commande annulee",
        )

    def test_foreign_laundry_cannot_confirm(self):
        owner = _make_laundry("0700020207", "own10@example.com")
        foreign = _make_laundry("0700020208", "foreign10@example.com")
        foreign_user = _make_laundry_user(foreign)
        order = _make_order("0700020010", owner, status="in_progress")
        _make_done_pickup_leg(order)

        self.client.force_login(foreign_user)
        resp = self.client.post(reverse("orders:laundry_weighing_confirm", args=[order.id]))

        order.refresh_from_db()
        self.assertNotEqual(resp.status_code, 500, "le rejet doit etre propre, pas un crash")
        self.assertIsNone(order.wash_complete_time, "aucune modification par une blanchisserie non proprietaire")
