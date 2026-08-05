"""
Audit parcours logistique V1 - Etape 2 : tests de caracterisation, aucune
correction de production. Couvre sync_order_status_from_legs et
sync_delivery_legs_for_order (item C, points 6/7/8 du plan).

Ces deux fonctions sont deja conformes aux regles metier visees ici -
tous les tests de ce fichier doivent deja passer avant toute correction.

Constat decouvert en ecrivant ces tests, hors liste A-G d'origine :
DeliveryLeg.save() (orders/models.py ~3806) force silencieusement une jambe
sans driver a repasser 'pending' si on tente de la mettre a
assigned/in_progress/done (garde-fou existant, correct, mais non documente
dans le plan initial). Pour construire des legs 'done'/'assigned' fideles a
leur etat reel, les fixtures ci-dessous affectent un driver a la jambe (pour
'assigned'/'in_progress') ou passent par un update() bas niveau qui ne
declenche pas ce garde-fou (pour forcer un 'done' sans dependre d'un driver
precis, cas volontairement neutre pour ces tests de sync).
"""
from django.test import TestCase

from orders.models import (
    Customer,
    DeliveryLeg,
    Order,
    sync_delivery_legs_for_order,
    sync_order_status_from_legs,
)
from partners.models import DeliveryPartner


def _make_order(phone, status="in_progress"):
    customer = Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")
    return Order.objects.create(customer=customer, status=status)


def _make_driver(phone):
    return DeliveryPartner.objects.create(name="Livreur Audit", phone=phone, is_active=True)


def _make_done_leg(order, leg_type):
    leg = DeliveryLeg.objects.create(order=order, leg_type=leg_type, status="pending")
    DeliveryLeg.objects.filter(pk=leg.pk).update(status="done")
    leg.refresh_from_db()
    return leg


class OrderFinalizationCharacterizationTests(TestCase):
    def test_pickup_done_return_not_done_order_stays_not_done(self):
        order = _make_order("0700009001")
        _make_done_leg(order, "pickup")
        driver = _make_driver("0700009101")
        DeliveryLeg.objects.create(order=order, leg_type="return", driver=driver, status="assigned")

        new_status = sync_order_status_from_legs(order, save=True)

        self.assertNotEqual(new_status, "done")
        order.refresh_from_db()
        self.assertNotEqual(order.status, "done")

    def test_pickup_done_and_return_done_order_becomes_done(self):
        order = _make_order("0700009002")
        _make_done_leg(order, "pickup")
        _make_done_leg(order, "return")

        new_status = sync_order_status_from_legs(order, save=True)

        self.assertEqual(new_status, "done")
        order.refresh_from_db()
        self.assertEqual(order.status, "done")

    def test_canceled_order_not_reactivated_by_sync_order_status_from_legs(self):
        order = _make_order("0700009003", status="canceled")
        driver_pickup = _make_driver("0700009102")
        driver_return = _make_driver("0700009103")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver_pickup, status="assigned")
        DeliveryLeg.objects.create(order=order, leg_type="return", driver=driver_return, status="in_progress")

        sync_order_status_from_legs(order, save=True)

        order.refresh_from_db()
        self.assertEqual(order.status, "canceled")

    def test_canceled_order_not_reactivated_by_sync_delivery_legs_for_order(self):
        order = _make_order("0700009004", status="canceled")
        driver = _make_driver("0700009104")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="assigned")

        sync_delivery_legs_for_order(order)

        order.refresh_from_db()
        self.assertEqual(order.status, "canceled")

    def test_ready_order_not_reverted_when_return_leg_created(self):
        """Etape 3, item 8 : ready doit rester preserve apres la creation/
        sauvegarde de la jambe return (garde-fou deja present dans
        sync_order_status_from_legs : status in ("canceled", "ready") -> no-op)."""
        order = _make_order("0700009005", status="ready")
        _make_done_leg(order, "pickup")
        DeliveryLeg.objects.create(order=order, leg_type="return", status="pending")

        new_status = sync_order_status_from_legs(order, save=True)

        self.assertEqual(new_status, "ready")
        order.refresh_from_db()
        self.assertEqual(order.status, "ready")
