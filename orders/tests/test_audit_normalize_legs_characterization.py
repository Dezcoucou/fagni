"""
Audit parcours logistique V1 - Etape 2 : tests de caracterisation, aucune
correction de production. Couvre les deux implementations de
normalize_order_legs (item E, points 12/13 du plan) :
- orders/views.py::normalize_order_legs (anti-doublons / conflits multi-driver)
- orders/service_layer/legs.py::normalize_order_legs (canonicalisation)

Constat decouvert en ecrivant ces tests, hors liste A-G d'origine : DeliveryLeg
porte une contrainte UNIQUE (order, leg_type) (migration 0048,
uniq_leg_per_order_type). Il ne peut donc jamais exister 2 legs "pickup" (ou
2 "return") actifs simultanement pour la meme commande, quel que soit le
driver. Le scenario "plusieurs legs actifs du meme type pour le driver cible,
on garde le plus recent" (bloc 2 de views.py::normalize_order_legs) est de ce
fait inatteignable avec le schema actuel : il ne peut jamais y avoir de
doublon a dedupliquer. Je ne fabrique donc pas ce scenario (il violerait une
contrainte reelle et ne testerait rien de reel) - je le signale ici plutot
que de l'executer. Le scenario reellement atteignable et teste ci-dessous est
la reassignation : quand order.delivery_partner change, le leg de l'ancien
driver (seul exemplaire de son leg_type) doit etre annule par le bloc 1
(sauf si deja paye).

Second constat, egalement hors A-G : DeliveryLeg.save() (orders/models.py
~3806) force silencieusement une jambe sans driver a rester 'pending' si on
tente de la passer a assigned/in_progress/done. Les fixtures ci-dessous
affectent donc un driver aux jambes pickup censees etre in_progress, et
utilisent un update() bas niveau pour poser une jambe 'done' sans dependre
d'un driver precis (cas neutre pour ce test de non-retrogradation).

Etape 3 : le gating return de service_layer/legs.py::normalize_order_legs a
ete corrige (le return reste 'pending' avant pickup done, meme avec un
driver deja affecte). test_return_leg_with_driver_stays_pending_before_pickup_done
remplace l'ancien test_return_leg_with_driver_currently_advances_despite_pickup_not_done
et exprime desormais la regle metier cible (vert apres correction).
"""
from decimal import Decimal

from django.test import TestCase

from orders.models import Customer, DeliveryLeg, Order
from orders.service_layer.legs import normalize_order_legs as service_normalize_order_legs
from orders.views import normalize_order_legs as views_normalize_order_legs
from partners.models import DeliveryPartner, LaundryPartner
from wallets.models import WalletTransaction
from wallets.services import get_or_create_wallet_for_delivery_partner


def _make_driver(phone):
    return DeliveryPartner.objects.create(name="Livreur Audit", phone=phone, is_active=True)


def _make_order(phone, delivery_partner=None):
    customer = Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")
    return Order.objects.create(customer=customer, status="in_progress", delivery_partner=delivery_partner)


class ViewsNormalizeOrderLegsDedupCharacterizationTests(TestCase):
    def test_previous_driver_leg_is_canceled_when_order_reassigned_to_new_driver(self):
        driver_old = _make_driver("0700009201")
        driver_new = _make_driver("0700009202")
        order = _make_order("0700009010", delivery_partner=driver_old)
        leg = DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver_old, status="assigned")

        order.delivery_partner = driver_new
        order.save(update_fields=["delivery_partner"])

        views_normalize_order_legs(order, driver=driver_new)

        leg.refresh_from_db()
        self.assertEqual(
            leg.status, "canceled",
            "le leg de l'ancien driver (non paye) doit etre annule quand la "
            "commande est reassignee a un nouveau driver",
        )

    def test_paid_leg_is_never_canceled_even_if_wrong_driver(self):
        driver_old = _make_driver("0700009203")
        driver_new = _make_driver("0700009204")
        order = _make_order("0700009011", delivery_partner=driver_old)
        leg = DeliveryLeg.objects.create(
            order=order, leg_type="pickup", driver=driver_old, status="done", driver_amount=Decimal("300"),
        )
        wallet = get_or_create_wallet_for_delivery_partner(driver_old)
        WalletTransaction.objects.create(
            wallet=wallet, order=order, leg=leg, type="payout", direction="in", amount=Decimal("300"),
        )

        order.delivery_partner = driver_new
        order.save(update_fields=["delivery_partner"])

        views_normalize_order_legs(order, driver=driver_new)

        leg.refresh_from_db()
        self.assertEqual(
            leg.status, "done",
            "un leg deja paye ne doit jamais etre annule, meme s'il appartient "
            "a un driver qui n'est plus order.delivery_partner",
        )


class ServiceLayerNormalizeOrderLegsCanonicalizationCharacterizationTests(TestCase):
    def test_pending_leg_with_driver_becomes_assigned(self):
        driver = _make_driver("0700009205")
        order = _make_order("0700009012")
        leg = DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="pending")

        service_normalize_order_legs(order)

        leg.refresh_from_db()
        self.assertEqual(leg.status, "assigned")

    def test_return_leg_without_driver_stays_pending_before_pickup_done(self):
        pickup_driver = _make_driver("0700009207")
        order = _make_order("0700009013")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=pickup_driver, status="in_progress")
        return_leg = DeliveryLeg.objects.create(order=order, leg_type="return", driver=None, status="pending")

        service_normalize_order_legs(order)

        return_leg.refresh_from_db()
        self.assertEqual(return_leg.status, "pending")

    def test_return_leg_with_driver_stays_pending_before_pickup_done(self):
        pickup_driver = _make_driver("0700009208")
        driver = _make_driver("0700009206")
        order = _make_order("0700009014")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=pickup_driver, status="in_progress")
        return_leg = DeliveryLeg.objects.create(order=order, leg_type="return", driver=driver, status="pending")

        service_normalize_order_legs(order)

        return_leg.refresh_from_db()
        self.assertEqual(
            return_leg.status, "pending",
            "le return doit rester pending tant que pickup n'est pas done, "
            "meme si un driver lui est deja affecte en base",
        )
        self.assertEqual(
            return_leg.driver_id, driver.id,
            "le driver deja affecte au leg return n'est pas retire par la "
            "normalisation - seul le statut reste pending",
        )

    def test_done_and_canceled_legs_never_downgraded(self):
        order = _make_order("0700009015")
        pickup_done = DeliveryLeg.objects.create(order=order, leg_type="pickup", status="pending")
        DeliveryLeg.objects.filter(pk=pickup_done.pk).update(status="done")
        pickup_done.refresh_from_db()
        return_canceled = DeliveryLeg.objects.create(order=order, leg_type="return", status="canceled")

        service_normalize_order_legs(order)

        pickup_done.refresh_from_db()
        return_canceled.refresh_from_db()
        self.assertEqual(pickup_done.status, "done")
        self.assertEqual(return_canceled.status, "canceled")
