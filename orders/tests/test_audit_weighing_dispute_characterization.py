"""
Audit parcours production/blanchisserie V1 - Etape 2 : tests de
caracterisation, aucune correction de production. Couvre
orders:laundry_weighing_dispute (orders/views.py::laundry_weighing_dispute).

Constats verifies directement (shell de test) avant ecriture :
- `OrderEvidencePhoto.objects.create(..., leg=leg_obj, ...)` reference une
  variable `leg_obj` qui n'existe nulle part dans cette fonction -> NameError
  a chaque appel, silencieusement avale par le `except Exception:` englobant
  -> AUCUNE OrderEvidencePhoto n'est jamais creee, quel que soit reason/image ;
- le `return HttpResponseRedirect(...)` final (succes ET rejet blanchisserie
  etrangere) leve une SECONDE NameError (HttpResponseRedirect non importe
  dans orders/views.py) -> la vue plante systematiquement (500) ;
- la fonction ne touche JAMAIS OrderWeighing (pas d'import, pas de write) :
  aucune transition de statut disputed n'est jamais appliquee.

On utilise Client(raise_request_exception=False) pour obtenir une reponse
HTTP exploitable (500) plutot qu'une ERROR de framework.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from orders.models import Customer, Order, OrderEvidencePhoto, OrderWeighing
from partners.models import LaundryPartner

User = get_user_model()


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_laundry(phone, email):
    return LaundryPartner.objects.create(name="Pressing Audit", phone=phone, email=email, is_active=True)


def _make_laundry_user(laundry):
    return User.objects.create_user(username=f"laundry_{laundry.id}", email=laundry.email, password="x")


def _make_order(phone, laundry, status="in_progress"):
    return Order.objects.create(
        customer=_make_customer(phone), laundry_partner=laundry, status=status,
        total_client_ttc=Decimal("1000"),
    )


class LaundryWeighingDisputeCharacterizationTests(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def test_dispute_with_reason_creates_evidence_issue(self):
        laundry = _make_laundry("0700040101", "own1@example.com")
        user = _make_laundry_user(laundry)
        order = _make_order("0700040001", laundry)
        OrderWeighing.objects.create(order=order, status="draft", weight_kg=Decimal("5.0"))

        self.client.force_login(user)
        self.client.post(reverse("orders:laundry_weighing_dispute", args=[order.id]), data={"reason": "poids incorrect"})

        self.assertEqual(
            OrderEvidencePhoto.objects.filter(order=order, kind="issue").count(), 1,
            "une contestation avec motif doit creer une OrderEvidencePhoto kind=issue",
        )

    def test_dispute_does_not_crash(self):
        """La fonction ne doit pas planter (ni a cause de leg_obj, ni pour
        toute autre raison). Constat : deux NameError distincts s'enchainent
        actuellement (leg_obj puis HttpResponseRedirect) -> 500."""
        laundry = _make_laundry("0700040102", "own2@example.com")
        user = _make_laundry_user(laundry)
        order = _make_order("0700040002", laundry)
        OrderWeighing.objects.create(order=order, status="draft", weight_kg=Decimal("5.0"))

        self.client.force_login(user)
        resp = self.client.post(reverse("orders:laundry_weighing_dispute", args=[order.id]), data={"reason": "poids incorrect"})

        self.assertNotEqual(resp.status_code, 500, "la vue ne doit jamais planter (NameError leg_obj ou autre)")

    def test_dispute_sets_weighing_status_disputed(self):
        laundry = _make_laundry("0700040103", "own3@example.com")
        user = _make_laundry_user(laundry)
        order = _make_order("0700040003", laundry)
        OrderWeighing.objects.create(order=order, status="draft", weight_kg=Decimal("5.0"))

        self.client.force_login(user)
        self.client.post(reverse("orders:laundry_weighing_dispute", args=[order.id]), data={"reason": "poids incorrect"})

        ow = OrderWeighing.objects.get(order=order)
        self.assertEqual(ow.status, "disputed", "la contestation doit positionner OrderWeighing.status='disputed'")

    def test_dispute_preserves_existing_weight(self):
        laundry = _make_laundry("0700040104", "own4@example.com")
        user = _make_laundry_user(laundry)
        order = _make_order("0700040004", laundry)
        OrderWeighing.objects.create(order=order, status="draft", weight_kg=Decimal("5.50"))

        self.client.force_login(user)
        self.client.post(reverse("orders:laundry_weighing_dispute", args=[order.id]), data={"reason": "poids incorrect"})

        ow = OrderWeighing.objects.get(order=order)
        self.assertEqual(ow.weight_kg, Decimal("5.50"), "le poids existant doit etre conserve")

    def test_foreign_laundry_cannot_dispute(self):
        owner = _make_laundry("0700040105", "own5@example.com")
        foreign = _make_laundry("0700040106", "foreign5@example.com")
        foreign_user = _make_laundry_user(foreign)
        order = _make_order("0700040005", owner)
        OrderWeighing.objects.create(order=order, status="draft", weight_kg=Decimal("5.0"))

        self.client.force_login(foreign_user)
        resp = self.client.post(reverse("orders:laundry_weighing_dispute", args=[order.id]), data={"reason": "x"})

        ow = OrderWeighing.objects.get(order=order)
        self.assertNotEqual(resp.status_code, 500, "le rejet doit etre propre, pas un crash")
        self.assertEqual(ow.status, "draft", "aucune modification par une blanchisserie non proprietaire")
        self.assertEqual(OrderEvidencePhoto.objects.filter(order=order).count(), 0)

    def test_resolved_weighing_cannot_be_moved_back_to_disputed(self):
        laundry = _make_laundry("0700040107", "own6@example.com")
        user = _make_laundry_user(laundry)
        order = _make_order("0700040006", laundry)
        OrderWeighing.objects.create(
            order=order, status="resolved", weight_kg=Decimal("5.0"), final_weight_kg=Decimal("6.0"),
        )

        self.client.force_login(user)
        self.client.post(reverse("orders:laundry_weighing_dispute", args=[order.id]), data={"reason": "encore un souci"})

        ow = OrderWeighing.objects.get(order=order)
        self.assertEqual(ow.status, "resolved", "une pesee resolue ne doit jamais repasser en dispute")

    def test_empty_reason_without_photo_is_refused_without_change(self):
        laundry = _make_laundry("0700040108", "own7@example.com")
        user = _make_laundry_user(laundry)
        order = _make_order("0700040007", laundry)
        OrderWeighing.objects.create(order=order, status="draft", weight_kg=Decimal("5.0"))

        self.client.force_login(user)
        resp = self.client.post(reverse("orders:laundry_weighing_dispute", args=[order.id]), data={"reason": ""})

        ow = OrderWeighing.objects.get(order=order)
        self.assertNotEqual(resp.status_code, 500, "raison vide sans photo doit etre refuse proprement, pas planter")
        self.assertEqual(OrderEvidencePhoto.objects.filter(order=order).count(), 0)
        self.assertEqual(ow.status, "draft", "aucun changement si raison vide et pas de photo")
