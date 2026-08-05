"""
Audit parcours client E2E V1 - tests de caracterisation, aucune correction
de production. Couvre le verrouillage d'une commande non-brouillon :
client_new_order_step2, client_new_order_step3, client_order_item_new,
client_order_item_edit, client_order_item_delete (orders/views.py).

Constat verifie directement en lisant le code avant d'ecrire ces tests :
AUCUNE de ces 5 vues ne verifie jamais order.is_draft. Toutes filtrent
uniquement sur l'ownership (customer__phone=phone) puis mutent
Order.pricing_mode/delivery_mode/bag_size/OrderItem et appellent
order.update_financials(save=True) ou ecrivent total_client_ttc
directement, quel que soit is_draft/payment_status/status. Une commande
deja confirmee (is_draft=False), partiellement payee ou totalement payee
peut donc voir son prix reellement modifie par le client apres coup.

Ces tests expriment le comportement METIER SECURISE attendu et echouent
donc majoritairement avant correction.
"""
from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from orders.models import Customer, Order, OrderItem

CLIENT_PHONE_COOKIE = "client_phone"


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_order(customer, total=Decimal("10000"), is_draft=False,
                 payment_status="unpaid", amount_paid=Decimal("0"),
                 status="in_progress", wave_checkout_id=""):
    return Order.objects.create(
        customer=customer,
        status=status,
        payment_status=payment_status,
        amount_paid=amount_paid,
        total_client_ttc=total,
        is_draft=is_draft,
        pricing_mode="bag",
        bag_size="medium",
        wave_checkout_id=wave_checkout_id,
    )


def _client_with_phone(phone):
    c = Client()
    c.cookies[CLIENT_PHONE_COOKIE] = phone
    return c


class LockedOrderRejectsStep2MutationTests(TestCase):
    def test_step2_refused_if_order_is_not_draft(self):
        customer = _make_customer("0700080001")
        order = _make_order(customer, is_draft=False)
        client = _client_with_phone("0700080001")
        original_delivery_mode = order.delivery_mode

        resp = client.post(
            reverse("orders:client_new_order_step2", args=[order.id]),
            data={"pricing_mode": "item", "delivery_mode": "express"},
        )

        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 200,
            "une commande non-brouillon ne doit jamais accepter de mutation step2",
        )
        self.assertEqual(order.pricing_mode, "bag")
        self.assertEqual(order.delivery_mode, original_delivery_mode)


class LockedOrderRejectsStep3MutationTests(TestCase):
    def test_step3_refused_if_order_is_not_draft(self):
        customer = _make_customer("0700080002")
        order = _make_order(customer, is_draft=False)
        client = _client_with_phone("0700080002")

        resp = client.post(
            reverse("orders:client_new_order_step3", args=[order.id]),
            data={"bag_size": "large", "confirm_bag_rules": "1"},
        )

        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 200,
            "une commande non-brouillon ne doit jamais accepter de mutation step3",
        )
        self.assertEqual(order.bag_size, "medium")


class LockedOrderRejectsItemMutationTests(TestCase):
    def test_item_new_refused_if_order_is_not_draft(self):
        customer = _make_customer("0700080003")
        order = _make_order(customer, is_draft=False, total=Decimal("10000"))
        client = _client_with_phone("0700080003")

        resp = client.post(
            reverse("orders:client_order_item_new", args=[order.id]),
            data={"designation": "Article ajoute apres verrouillage", "quantity": "3", "unit_price": "5000"},
        )

        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 200,
            "une commande non-brouillon ne doit jamais accepter l'ajout d'un article",
        )
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 0)
        self.assertEqual(order.total_client_ttc, Decimal("10000"))

    def test_item_edit_refused_if_order_is_not_draft(self):
        customer = _make_customer("0700080004")
        order = _make_order(customer, is_draft=False, total=Decimal("10000"))
        item = OrderItem.objects.create(
            order=order, service=None, designation="Article initial",
            quantity=Decimal("1"), unit_price=Decimal("10000"), total=Decimal("10000"),
        )
        client = _client_with_phone("0700080004")

        resp = client.post(
            reverse("orders:client_order_item_edit", args=[order.id, item.id]),
            data={"designation": "Article modifie", "quantity": "10", "unit_price": "99999"},
        )

        item.refresh_from_db()
        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 200,
            "une commande non-brouillon ne doit jamais accepter la modification d'un article",
        )
        self.assertEqual(item.designation, "Article initial")
        self.assertEqual(item.quantity, Decimal("1"))
        self.assertEqual(order.total_client_ttc, Decimal("10000"))

    def test_item_delete_refused_if_order_is_not_draft(self):
        customer = _make_customer("0700080005")
        order = _make_order(customer, is_draft=False, total=Decimal("10000"))
        item = OrderItem.objects.create(
            order=order, service=None, designation="Article initial",
            quantity=Decimal("1"), unit_price=Decimal("10000"), total=Decimal("10000"),
        )
        client = _client_with_phone("0700080005")

        resp = client.post(
            reverse("orders:client_order_item_delete", args=[order.id, item.id]),
        )

        order.refresh_from_db()
        self.assertNotEqual(
            resp.status_code, 200,
            "une commande non-brouillon ne doit jamais accepter la suppression d'un article",
        )
        self.assertTrue(OrderItem.objects.filter(pk=item.pk).exists())
        self.assertEqual(order.total_client_ttc, Decimal("10000"))


class PartiallyPaidOrderRejectsMutationTests(TestCase):
    def test_item_new_refused_after_partial_payment(self):
        customer = _make_customer("0700080006")
        order = _make_order(
            customer, is_draft=False, total=Decimal("10000"),
            payment_status="partial", amount_paid=Decimal("4000"),
        )
        client = _client_with_phone("0700080006")

        client.post(
            reverse("orders:client_order_item_new", args=[order.id]),
            data={"designation": "Article apres acompte", "quantity": "1", "unit_price": "50000"},
        )

        order.refresh_from_db()
        self.assertEqual(order.total_client_ttc, Decimal("10000"), "aucune mutation apres paiement partiel")
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 0)


class FullyPaidOrderRejectsMutationTests(TestCase):
    def test_item_new_refused_after_full_payment(self):
        customer = _make_customer("0700080007")
        order = _make_order(
            customer, is_draft=False, total=Decimal("10000"),
            payment_status="paid", amount_paid=Decimal("10000"),
        )
        client = _client_with_phone("0700080007")

        client.post(
            reverse("orders:client_order_item_new", args=[order.id]),
            data={"designation": "Article apres solde", "quantity": "1", "unit_price": "50000"},
        )

        order.refresh_from_db()
        self.assertEqual(order.total_client_ttc, Decimal("10000"), "aucune mutation apres paiement total")
        self.assertEqual(OrderItem.objects.filter(order=order).count(), 0)


class ActiveWaveCheckoutFreezesPriceTests(TestCase):
    def test_item_new_refused_when_wave_checkout_active(self):
        """Une session Wave Checkout active (montant deja communique a
        Wave) doit geler le prix. Note importante : order.update_financials()
        (appele par client_order_item_new) porte deja son PROPRE verrou
        independant ("une fois cree, le prix client n'est jamais recalcule
        par update_financials() si total_client_ttc > 0") - donc une simple
        creation d'article ne fait jamais bouger total_client_ttc, avec ou
        sans wave_checkout_id. Le SEUL chemin qui ecrase reellement
        total_client_ttc dans cette vue est l'injection upsell_data en
        session (voir client_order_item_new, bloc upsell) : c'est ce chemin
        qui est teste ici pour caracteriser reellement l'absence de garde
        wave_checkout_id (le verrou update_financials ne protege pas ce
        second chemin d'ecriture)."""
        customer = _make_customer("0700080008")
        order = _make_order(
            customer, is_draft=False, total=Decimal("10000"),
            wave_checkout_id="checkout_test_abc123",
        )
        from django.utils import timezone
        order.wave_checkout_created_at = timezone.now()
        order.save(update_fields=["wave_checkout_created_at"])
        client = _client_with_phone("0700080008")

        session = client.session
        session["upsell_data"] = {"express_24h": True, "premium_ironing": True, "fragrance": False, "delicate_care": False}
        session.save()

        client.post(
            reverse("orders:client_order_item_new", args=[order.id]),
            data={"designation": "Article apres wave checkout", "quantity": "1", "unit_price": "1"},
        )

        order.refresh_from_db()
        self.assertEqual(
            order.total_client_ttc, Decimal("10000"),
            "total_client_ttc ne doit pas bouger (meme via l'injection upsell) tant qu'une session Wave Checkout est active",
        )


class TotalUnchangedAfterLockTests(TestCase):
    def test_total_client_ttc_unchanged_after_lock_across_all_mutation_routes(self):
        """Test global : enchaine les 5 routes de mutation sur une commande
        verrouillee (is_draft=False) et verifie que le prix locke ne bouge
        jamais, quelle que soit la route tentee."""
        customer = _make_customer("0700080009")
        order = _make_order(customer, is_draft=False, total=Decimal("15000"))
        item = OrderItem.objects.create(
            order=order, service=None, designation="Article initial",
            quantity=Decimal("1"), unit_price=Decimal("15000"), total=Decimal("15000"),
        )
        client = _client_with_phone("0700080009")
        locked_price = order.total_client_ttc

        client.post(reverse("orders:client_new_order_step2", args=[order.id]),
                    data={"pricing_mode": "item", "delivery_mode": "express"})
        client.post(reverse("orders:client_new_order_step3", args=[order.id]),
                    data={"bag_size": "large", "confirm_bag_rules": "1"})
        client.post(reverse("orders:client_order_item_new", args=[order.id]),
                    data={"designation": "Injection", "quantity": "5", "unit_price": "999999"})
        client.post(reverse("orders:client_order_item_edit", args=[order.id, item.id]),
                    data={"designation": "Injection2", "quantity": "5", "unit_price": "999999"})
        client.post(reverse("orders:client_order_item_delete", args=[order.id, item.id]))

        order.refresh_from_db()
        self.assertEqual(
            order.total_client_ttc, locked_price,
            "total_client_ttc doit rester strictement identique apres verrouillage, quelle que soit la route tentee",
        )
