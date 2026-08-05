"""
Audit parcours logistique V1 - Etape 2 : tests de caracterisation, aucune
correction de production. Couvre trigger_driver_payout_for_leg (item D /
bug G du diagnostic) : le fallback generique order.delivery_partner ne doit
jamais servir a payer une jambe pickup sans driver direct.

Les tests 9 et 11 encodent le comportement METIER ATTENDU et echouent avant
correction (bug G) - le code actuel utilise order.delivery_partner comme
fallback pour N'IMPORTE QUEL type de jambe. Le test 10 caracterise un
comportement deja correct pour une jambe return (inchange par la future
correction).

Deux constats decouverts en ecrivant ces tests, hors liste A-G d'origine :
1) Order.save() declenche automatiquement sync_delivery_legs_for_order quand
   payment_status devient 'paid' (orders/models.py, bloc AUTO-REPAIR), ce qui
   cree deja les legs pickup/return de la commande. On les supprime donc
   juste apres creation, avant de poser nos propres legs de test (meme
   convention que test_leg_payout.py).
2) DeliveryLeg.save() force silencieusement une jambe sans driver a rester
   'pending' si on tente de la passer a assigned/in_progress/done (garde-fou
   existant, correct). Pour caracteriser trigger_driver_payout_for_leg sur
   une jambe reellement 'done' sans driver direct, on cree la jambe 'pending'
   puis on force son statut a 'done' via un update() bas niveau (bypass
   volontaire de ce garde-fou, propre a la construction du fixture de test -
   la fonction testee ne se soucie que de l'etat final de la jambe).
"""
from decimal import Decimal

from django.test import TestCase

from orders.models import Customer, DeliveryLeg, Order
from orders.service_layer.payouts import trigger_driver_payout_for_leg
from partners.models import DeliveryPartner, LaundryPartner
from wallets.models import WalletTransaction


def _make_driver(phone):
    return DeliveryPartner.objects.create(name="Livreur Audit", phone=phone, is_active=True)


def _make_paid_order(phone, pickup_driver=None, delivery_partner=None):
    customer = Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")
    laundry = LaundryPartner.objects.filter(id__isnull=False).first() or LaundryPartner.objects.create(
        name="Pressing Audit", phone="0700009900", is_active=True,
    )
    order = Order.objects.create(
        customer=customer,
        laundry_partner=laundry,
        total_client_ttc=Decimal("1000"),
        amount_paid=Decimal("1000"),
        payment_status="paid",
        pickup_driver=pickup_driver,
        delivery_partner=delivery_partner,
    )
    DeliveryLeg.objects.filter(order=order).delete()
    return order


def _make_done_leg_without_driver(order, leg_type, driver_amount):
    leg = DeliveryLeg.objects.create(
        order=order, leg_type=leg_type, driver=None, status="pending", driver_amount=driver_amount,
    )
    DeliveryLeg.objects.filter(pk=leg.pk).update(status="done")
    leg.refresh_from_db()
    return leg


class PayoutLegTypeFallbackCharacterizationTests(TestCase):
    def test_pickup_leg_done_without_driver_should_pay_pickup_driver_not_delivery_partner(self):
        driver_a = _make_driver("0700009101")  # order.pickup_driver
        driver_b = _make_driver("0700009102")  # order.delivery_partner (jambe retour)
        order = _make_paid_order("0700009001", pickup_driver=driver_a, delivery_partner=driver_b)

        leg = _make_done_leg_without_driver(order, "pickup", Decimal("300"))

        trigger_driver_payout_for_leg(leg)

        tx_a = WalletTransaction.objects.filter(
            wallet__owner_type="driver", wallet__delivery_partner=driver_a,
            order=order, leg=leg, type="payout", direction="in",
        )
        tx_b = WalletTransaction.objects.filter(
            wallet__owner_type="driver", wallet__delivery_partner=driver_b,
            order=order, leg=leg, type="payout", direction="in",
        )
        self.assertTrue(
            tx_a.exists(),
            "une jambe pickup sans driver direct doit payer order.pickup_driver",
        )
        self.assertFalse(
            tx_b.exists(),
            "order.delivery_partner ne doit jamais payer une jambe pickup",
        )

    def test_return_leg_done_without_driver_pays_delivery_partner(self):
        driver_a = _make_driver("0700009103")  # order.pickup_driver (non pertinent ici)
        driver_b = _make_driver("0700009104")  # order.delivery_partner
        order = _make_paid_order("0700009002", pickup_driver=driver_a, delivery_partner=driver_b)

        leg = _make_done_leg_without_driver(order, "return", Decimal("200"))

        trigger_driver_payout_for_leg(leg)

        tx_b = WalletTransaction.objects.filter(
            wallet__owner_type="driver", wallet__delivery_partner=driver_b,
            order=order, leg=leg, type="payout", direction="in",
        )
        self.assertTrue(tx_b.exists(), "une jambe return sans driver direct paie order.delivery_partner")

    def test_pickup_leg_done_without_any_driver_reference_creates_no_payout(self):
        driver_b = _make_driver("0700009105")  # order.delivery_partner, ne doit jamais servir
        order = _make_paid_order("0700009003", pickup_driver=None, delivery_partner=driver_b)

        leg = _make_done_leg_without_driver(order, "pickup", Decimal("300"))

        trigger_driver_payout_for_leg(leg)

        self.assertFalse(
            WalletTransaction.objects.filter(order=order, leg=leg, type="payout", direction="in").exists(),
            "sans leg.driver ni order.pickup_driver, aucun payout ne doit etre cree "
            "(order.delivery_partner ne doit jamais etre utilise comme fallback pickup)",
        )
