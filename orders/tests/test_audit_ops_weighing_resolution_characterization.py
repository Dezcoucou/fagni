"""
Audit parcours production/blanchisserie V1 - Etape 2 : tests de
caracterisation, aucune correction de production. Couvre
orders:ops_weighing_resolve (orders/views.py::ops_weighing_resolve).

Constats verifies directement (shell de test) avant ecriture :
- seul @login_required protege la vue : AUCUNE verification is_staff.
  Un utilisateur authentifie non-staff a pu, dans le probe, resoudre une
  pesee "draft" (statut cense etre exclusivement reserve a OPS) ;
- le garde-fou `if request.method != "POST" and ow.status != "disputed":
  redirect(...)` ne s'applique QUE sur GET : sur POST, la condition est
  toujours fausse (`request.method != "POST"` est False), donc la resolution
  s'applique quel que soit le statut reel (draft/confirmed/resolved) ;
- le POST exige un champ "address" (copie-collee depuis une vue de creation
  de commande client, sans rapport avec la resolution de litige pesee) :
  sans ce champ, la requete est detournee vers le template
  client_new_order.html au lieu de traiter la resolution ;
- le calcul de final_weight_kg > 0 est deja correct (verifie) ;
- l'ecriture des champs de resolution (status/final_weight_kg/resolved_by/
  resolved_at/resolution_notes) est deja correcte une fois le chemin atteint ;
- le contexte de rendu GET reference une variable `snapshot` jamais definie
  -> NameError sur tout GET d'une pesee "disputed" (seul cas ou le GET
  atteint le render()).

On utilise Client(raise_request_exception=False) pour les cas connus pour
planter (GET disputed, POST sans address), afin d'obtenir une reponse HTTP
exploitable plutot qu'une ERROR de framework.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from orders.models import Customer, Order, OrderWeighing

User = get_user_model()


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_order(phone, status="in_progress"):
    return Order.objects.create(customer=_make_customer(phone), status=status, total_client_ttc=Decimal("1000"))


def _make_staff_user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="x", is_staff=True)


def _make_non_staff_user(name):
    return User.objects.create_user(username=name, email=f"{name}@example.com", password="x", is_staff=False)


def _resolve_post(client, order, weight="7.5", notes="note", address="Riviera 3"):
    data = {"final_weight_kg": weight, "resolution_notes": notes}
    if address is not None:
        data["address"] = address
    return client.post(reverse("orders:ops_weighing_resolve", args=[order.id]), data=data)


class OpsWeighingResolveCharacterizationTests(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def test_non_staff_user_is_refused(self):
        order = _make_order("0700050001")
        ow = OrderWeighing.objects.create(order=order, status="disputed", weight_kg=Decimal("5.0"))
        user = _make_non_staff_user("nonstaff1")

        self.client.force_login(user)
        _resolve_post(self.client, order)

        ow.refresh_from_db()
        self.assertEqual(ow.status, "disputed", "un utilisateur non-staff ne doit jamais pouvoir resoudre un litige")

    def test_staff_user_is_authorized(self):
        order = _make_order("0700050002")
        ow = OrderWeighing.objects.create(order=order, status="disputed", weight_kg=Decimal("5.0"))
        user = _make_staff_user("staff1")

        self.client.force_login(user)
        _resolve_post(self.client, order)

        ow.refresh_from_db()
        self.assertEqual(ow.status, "resolved", "un utilisateur staff doit pouvoir resoudre un litige")

    def test_only_disputed_weighing_can_be_resolved_get_redirects(self):
        """Le garde-fou fonctionne deja correctement pour GET (deja-correct)."""
        order = _make_order("0700050003")
        OrderWeighing.objects.create(order=order, status="draft", weight_kg=Decimal("5.0"))
        user = _make_staff_user("staff2")

        self.client.force_login(user)
        resp = self.client.get(reverse("orders:ops_weighing_resolve", args=[order.id]))

        self.assertEqual(resp.status_code, 302, "un GET sur une pesee non-disputee doit rediriger sans planter")

    def test_post_on_draft_is_refused(self):
        order = _make_order("0700050004")
        ow = OrderWeighing.objects.create(order=order, status="draft", weight_kg=Decimal("5.0"))
        user = _make_staff_user("staff3")

        self.client.force_login(user)
        _resolve_post(self.client, order)

        ow.refresh_from_db()
        self.assertEqual(ow.status, "draft", "un POST direct sur une pesee draft doit etre refuse")
        self.assertIsNone(ow.final_weight_kg)

    def test_post_on_confirmed_is_refused(self):
        order = _make_order("0700050005")
        ow = OrderWeighing.objects.create(order=order, status="confirmed", weight_kg=Decimal("5.0"))
        user = _make_staff_user("staff4")

        self.client.force_login(user)
        _resolve_post(self.client, order)

        ow.refresh_from_db()
        self.assertEqual(ow.status, "confirmed", "un POST direct sur une pesee confirmed doit etre refuse")
        self.assertIsNone(ow.final_weight_kg)

    def test_post_on_resolved_is_idempotent_or_refused_without_rewrite(self):
        order = _make_order("0700050006")
        ow = OrderWeighing.objects.create(
            order=order, status="resolved", weight_kg=Decimal("5.0"),
            final_weight_kg=Decimal("10.0"), resolution_notes="notes originales",
        )
        user = _make_staff_user("staff5")

        self.client.force_login(user)
        _resolve_post(self.client, order, weight="99.0", notes="nouvelle note")

        ow.refresh_from_db()
        self.assertEqual(
            ow.final_weight_kg, Decimal("10.0"),
            "un POST direct sur une pesee resolved ne doit jamais reecrire final_weight_kg",
        )
        self.assertEqual(ow.resolution_notes, "notes originales")

    def test_no_address_field_required(self):
        order = _make_order("0700050007")
        ow = OrderWeighing.objects.create(order=order, status="disputed", weight_kg=Decimal("5.0"))
        user = _make_staff_user("staff6")

        self.client.force_login(user)
        _resolve_post(self.client, order, address=None)

        ow.refresh_from_db()
        self.assertEqual(
            ow.status, "resolved",
            "aucun champ 'address' ne doit etre necessaire pour resoudre un litige pesee",
        )

    def test_final_weight_must_be_strictly_positive(self):
        order = _make_order("0700050008")
        ow = OrderWeighing.objects.create(order=order, status="disputed", weight_kg=Decimal("5.0"))
        user = _make_staff_user("staff7")

        self.client.force_login(user)
        _resolve_post(self.client, order, weight="0")

        ow.refresh_from_db()
        self.assertEqual(ow.status, "disputed", "un poids final <= 0 doit etre refuse")
        self.assertIsNone(ow.final_weight_kg)

    def test_successful_resolution_sets_all_fields(self):
        order = _make_order("0700050009")
        ow = OrderWeighing.objects.create(order=order, status="disputed", weight_kg=Decimal("5.0"))
        user = _make_staff_user("staff8")

        self.client.force_login(user)
        _resolve_post(self.client, order, weight="8.25", notes="ecart balance confirme")

        ow.refresh_from_db()
        self.assertEqual(ow.status, "resolved")
        self.assertEqual(ow.final_weight_kg, Decimal("8.25"))
        self.assertEqual(ow.resolved_by_id, user.id)
        self.assertIsNotNone(ow.resolved_at)
        self.assertEqual(ow.resolution_notes, "ecart balance confirme")

    def test_get_disputed_weighing_does_not_crash_with_nameerror(self):
        order = _make_order("0700050010")
        OrderWeighing.objects.create(order=order, status="disputed", weight_kg=Decimal("5.0"))
        user = _make_staff_user("staff9")

        self.client.force_login(user)
        resp = self.client.get(reverse("orders:ops_weighing_resolve", args=[order.id]))

        self.assertEqual(resp.status_code, 200, "le GET d'une pesee disputed ne doit pas planter (NameError snapshot)")
