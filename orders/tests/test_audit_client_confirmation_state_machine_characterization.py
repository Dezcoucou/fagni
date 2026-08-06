"""
Audit parcours client E2E V1 - tests de caracterisation, aucune correction
de production. Couvre client_new_order_step4 (confirmation finale,
orders/views.py) : CGU, idempotence, isolation des cles de session du
wizard, et couplage confirmation/paiement.

Constats verifies directement en lisant le code avant d'ecrire ces tests :
- request.session["client_wizard_pricing_mode"], ["client_wizard_category_id"]
  et ["upsell_data"] sont des cles GLOBALES (non prefixees par order.id) :
  n'importe quelle commande brouillon suivante dans la meme session recoit
  les valeurs laissees par la commande precedente ;
- client_new_order_step4 ne verifie JAMAIS order.payment_status ni
  order.amount_paid avant d'assigner pressing/livreur et de faire passer
  Order.status a "in_progress" (seule condition: status=="pending" ET
  (laundry ou driver assigne)) ;
- client_new_order_step4 ne verifie JAMAIS order.status=="canceled" avant
  de creer les DeliveryLeg pickup/return et de poser is_draft=False ;
- le garde CGU (accepted_cgu != "1" -> redirect) est deja correct, et le
  garde "if not is_draft: redirect" en tete de vue rend deja la double
  confirmation naturellement idempotente - ces deux points sont
  caracterises comme deja-corrects, pas comme des bugs.

Les moteurs d'affectation (pick_best_laundry/pick_best_driver) sont
mockes : aucune dependance a la geolocalisation reelle.
"""
from decimal import Decimal
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from orders.models import Customer, DeliveryLeg, Order, OrderItem, OrderUpsell
from partners.models import DeliveryPartner, LaundryPartner

CLIENT_PHONE_COOKIE = "client_phone"


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_draft_order(customer, total=Decimal("10000"), status="pending"):
    return Order.objects.create(
        customer=customer,
        status=status,
        payment_status="unpaid",
        amount_paid=Decimal("0"),
        total_client_ttc=total,
        is_draft=True,
        pricing_mode="bag",
        bag_size="medium",
    )


def _client_with_phone(phone):
    c = Client()
    c.cookies[CLIENT_PHONE_COOKIE] = phone
    return c


def _confirm(client, order, accepted_cgu="1"):
    data = {"bag_size": "medium"}
    if accepted_cgu is not None:
        data["accepted_cgu"] = accepted_cgu
    return client.post(reverse("orders:client_new_order_step4", args=[order.id]), data=data)


class ConfirmationCguGuardTests(TestCase):
    def test_confirmation_without_cgu_is_refused(self):
        customer = _make_customer("0700100001")
        order = _make_draft_order(customer)
        client = _client_with_phone("0700100001")

        with patch("orders.assignment.pick_best_laundry", return_value=(None, None)), \
             patch("orders.assignment.pick_best_driver", return_value=(None, None)):
            _confirm(client, order, accepted_cgu=None)

        order.refresh_from_db()
        self.assertTrue(order.is_draft, "sans acceptation des CGU, la commande doit rester en brouillon")
        self.assertFalse(DeliveryLeg.objects.filter(order=order).exists())


class DoubleConfirmationIdempotentTests(TestCase):
    def test_double_confirmation_is_idempotent(self):
        customer = _make_customer("0700100002")
        order = _make_draft_order(customer)
        client = _client_with_phone("0700100002")

        with patch("orders.assignment.pick_best_laundry", return_value=(None, None)), \
             patch("orders.assignment.pick_best_driver", return_value=(None, None)):
            _confirm(client, order)
            order.refresh_from_db()
            self.assertFalse(order.is_draft)
            _confirm(client, order)

        self.assertEqual(DeliveryLeg.objects.filter(order=order, leg_type="pickup").count(), 1)
        self.assertEqual(DeliveryLeg.objects.filter(order=order, leg_type="return").count(), 1)


class NoNewLegsAfterLockTests(TestCase):
    def test_no_new_delivery_leg_created_after_lock_even_if_legs_missing(self):
        """Une fois la commande verrouillee (is_draft=False), aucune
        nouvelle DeliveryLeg ne doit jamais etre creee par une nouvelle
        tentative de confirmation - meme si les legs existantes ont ete
        supprimees entre-temps (ex: nettoyage OPS)."""
        customer = _make_customer("0700100010")
        order = _make_draft_order(customer)
        client = _client_with_phone("0700100010")

        with patch("orders.assignment.pick_best_laundry", return_value=(None, None)), \
             patch("orders.assignment.pick_best_driver", return_value=(None, None)):
            _confirm(client, order)

        order.refresh_from_db()
        self.assertFalse(order.is_draft)
        DeliveryLeg.objects.filter(order=order).delete()
        self.assertEqual(DeliveryLeg.objects.filter(order=order).count(), 0)

        with patch("orders.assignment.pick_best_laundry", return_value=(None, None)), \
             patch("orders.assignment.pick_best_driver", return_value=(None, None)):
            _confirm(client, order)

        self.assertEqual(
            DeliveryLeg.objects.filter(order=order).count(), 0,
            "une commande verrouillee (is_draft=False) ne doit plus jamais pouvoir (re)creer de DeliveryLeg via la confirmation",
        )


class CguIsolationTests(TestCase):
    def test_cgu_acceptance_isolated_by_order_id(self):
        customer = _make_customer("0700100011")
        order_a = _make_draft_order(customer)
        order_b = _make_draft_order(customer)
        client = _client_with_phone("0700100011")

        with patch("orders.assignment.pick_best_laundry", return_value=(None, None)), \
             patch("orders.assignment.pick_best_driver", return_value=(None, None)):
            _confirm(client, order_a)

        session = client.session
        self.assertTrue(session.get(f"client_cgu_accepted_{order_a.id}", False))
        self.assertFalse(
            session.get(f"client_cgu_accepted_{order_b.id}", False),
            "l'acceptation des CGU pour la commande A ne doit jamais couvrir la commande B",
        )


class WizardSessionIsolationTests(TestCase):
    def test_client_wizard_pricing_mode_isolated_by_order_id(self):
        customer = _make_customer("0700100003")
        order_a = _make_draft_order(customer)
        order_b = _make_draft_order(customer)
        client = _client_with_phone("0700100003")

        client.post(reverse("orders:client_new_order_step2", args=[order_a.id]),
                    data={"pricing_mode": "item", "delivery_mode": "standard"})

        session = client.session
        self.assertNotIn(
            "client_wizard_pricing_mode", session,
            "la cle de session doit etre isolee par order.id (ex: client_wizard_pricing_mode_<id>), "
            "pas globale et partagee entre commandes",
        )
        order_b.refresh_from_db()
        self.assertEqual(
            order_b.pricing_mode, "bag",
            "le choix pricing_mode de la commande A ne doit jamais influencer une autre commande B",
        )

    def test_client_wizard_category_id_isolated_by_order_id(self):
        from orders.models import ServiceCategory

        customer = _make_customer("0700100004")
        cat_a = ServiceCategory.objects.create(name="Categorie A")
        order_a = _make_draft_order(customer)
        order_b = _make_draft_order(customer)
        client = _client_with_phone("0700100004")

        client.post(reverse("orders:client_new_order_step2", args=[order_a.id]),
                    data={"pricing_mode": "item", "category_id": str(cat_a.id), "delivery_mode": "standard"})

        resp = client.get(reverse("orders:client_new_order_step3", args=[order_b.id]))

        self.assertNotEqual(
            resp.context["category"].id if resp.context.get("category") else None,
            cat_a.id,
            "la categorie choisie pour la commande A ne doit jamais etre reutilisee pour la commande B",
        )

    def test_upsell_data_isolated_by_order_id(self):
        customer = _make_customer("0700100005")
        order_a_upsell = {"express_24h": True, "premium_ironing": True, "fragrance": False, "delicate_care": False}
        order_b = _make_draft_order(customer, total=Decimal("5000"))
        client = _client_with_phone("0700100005")

        session = client.session
        session["upsell_data"] = order_a_upsell
        session.save()

        client.post(
            reverse("orders:client_order_item_new", args=[order_b.id]),
            data={"designation": "Article B", "quantity": "1", "unit_price": "5000"},
        )

        order_b.refresh_from_db()
        upsell_b = OrderUpsell.objects.filter(order=order_b).first()
        self.assertTrue(
            upsell_b is None or not upsell_b.express_24h,
            "les upsells choisis pour la commande A ne doivent jamais s'appliquer a la commande B",
        )
        self.assertEqual(
            order_b.total_client_ttc,
            Decimal("5000"),
            (
                "le total verrouillé de B doit rester inchangé et ne doit "
                "jamais intégrer les upsells appartenant à une autre commande"
            ),
        )


class UnpaidOrderCannotMobilizeResourcesTests(TestCase):
    def _confirm_with_assignment(self, client, order):
        laundry = LaundryPartner.objects.create(name="Pressing Audit", phone="0700100097", is_active=True)
        driver = DeliveryPartner.objects.create(name="Livreur Audit", phone="0700100098", is_active=True)
        with patch("orders.assignment.pick_best_laundry", return_value=(laundry, "match")), \
             patch("orders.assignment.pick_best_driver", return_value=(driver, "match")):
            _confirm(client, order)
        return laundry, driver

    def test_unpaid_order_not_set_in_progress_after_confirmation(self):
        customer = _make_customer("0700100006")
        order = _make_draft_order(customer)
        client = _client_with_phone("0700100006")

        self._confirm_with_assignment(client, order)

        order.refresh_from_db()
        self.assertNotEqual(
            order.status, "in_progress",
            "une commande non payee ne doit pas etre mise in_progress a la confirmation",
        )

    def test_unpaid_order_does_not_mobilize_laundry_or_driver(self):
        customer = _make_customer("0700100007")
        order = _make_draft_order(customer)
        client = _client_with_phone("0700100007")

        self._confirm_with_assignment(client, order)

        order.refresh_from_db()
        self.assertIsNone(
            order.laundry_partner_id,
            "une commande non payee ne doit pas mobiliser de pressing a la confirmation",
        )
        self.assertIsNone(
            order.delivery_partner_id,
            "une commande non payee ne doit pas mobiliser de livreur a la confirmation",
        )

    def test_no_active_delivery_leg_starts_before_payment_confirmed(self):
        customer = _make_customer("0700100008")
        order = _make_draft_order(customer)
        client = _client_with_phone("0700100008")

        self._confirm_with_assignment(client, order)

        active_legs = DeliveryLeg.objects.filter(order=order).exclude(status__in=("pending", "canceled"))
        self.assertFalse(
            active_legs.exists(),
            "aucune DeliveryLeg ne doit devenir active (assigned/in_progress/done) avant paiement confirme",
        )


class CanceledOrderNeverConfirmedTests(TestCase):
    def test_canceled_order_cannot_be_confirmed_or_reactivated(self):
        customer = _make_customer("0700100009")
        order = _make_draft_order(customer, status="canceled")
        client = _client_with_phone("0700100009")

        with patch("orders.assignment.pick_best_laundry", return_value=(None, None)), \
             patch("orders.assignment.pick_best_driver", return_value=(None, None)):
            _confirm(client, order)

        order.refresh_from_db()
        self.assertEqual(order.status, "canceled", "une commande annulee ne doit jamais etre reactivee par la confirmation")
        self.assertTrue(order.is_draft, "une commande annulee ne doit jamais pouvoir etre confirmee (is_draft doit rester True)")
        self.assertFalse(DeliveryLeg.objects.filter(order=order).exists())
