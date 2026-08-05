"""
Audit parcours logistique V1 - Etape 2 : tests de caracterisation, aucune
correction de production. Couvre driver_confirm_pickup (item A.1) et
api_driver_dropoff (item A.2) du plan d'audit.

Choix explicite pour l'evenement pickup.done/pickup.collected (item A.1,
dernier point) : le test encode le comportement METIER ATTENDU
("pickup.collected"), pas l'etat actuel. Il echoue donc avant correction,
au meme titre que les bugs A/B/G - coherent avec le reste de la mission.

Constat non prevu dans le diagnostic initial (bugs A-G) : api_driver_dropoff
ne verifie pas que le livreur qui depose est bien celui affecte a la jambe
pickup. Le test correspondant echoue et documente ce trou, sans le corriger -
signale separement, pas dans la liste A-G d'origine.
"""
import jwt
from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from orders.models import Customer, Order, DeliveryLeg
from partners.models import DeliveryPartner, LaundryPartner


def _token_driver(driver):
    return jwt.encode({'did': driver.id, 'name': driver.name}, settings.SECRET_KEY, algorithm='HS256')


def _driver_headers(driver):
    return {'HTTP_AUTHORIZATION': f'Bearer {_token_driver(driver)}'}


def _make_driver(phone):
    return DeliveryPartner.objects.create(name="Livreur Test", phone=phone, is_active=True)


def _make_order(customer_phone="0700007001"):
    customer = Customer.objects.create(name="Client Test", phone=customer_phone, address="Riviera 3")
    return Order.objects.create(customer=customer, status="in_progress")


class DriverConfirmPickupCharacterizationTests(TestCase):
    def test_confirm_pickup_moves_leg_to_in_progress(self):
        driver = _make_driver("0700007101")
        order = _make_order("0700007002")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="assigned")

        resp = self.client.post(
            reverse("api-driver-pickup", args=[order.id]),
            data={"articles_count": 5},
            **_driver_headers(driver),
        )
        self.assertEqual(resp.status_code, 200)

        leg = DeliveryLeg.objects.get(order=order, leg_type="pickup")
        self.assertEqual(leg.status, "in_progress")

    def test_confirm_pickup_never_sets_leg_done(self):
        driver = _make_driver("0700007102")
        order = _make_order("0700007003")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="assigned")

        self.client.post(
            reverse("api-driver-pickup", args=[order.id]),
            data={"articles_count": 5},
            **_driver_headers(driver),
        )

        leg = DeliveryLeg.objects.get(order=order, leg_type="pickup")
        self.assertNotEqual(leg.status, "done")

    def test_confirm_pickup_should_log_pickup_collected_not_pickup_done(self):
        driver = _make_driver("0700007103")
        order = _make_order("0700007004")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="assigned")

        self.client.post(
            reverse("api-driver-pickup", args=[order.id]),
            data={"articles_count": 5},
            **_driver_headers(driver),
        )

        from orders.models import FagniEvent
        event = FagniEvent.objects.filter(order=order, actor_type="driver").order_by("-id").first()
        self.assertIsNotNone(event, "aucun evenement pickup logue")
        self.assertEqual(
            event.event_type, "pickup.collected",
            "la jambe pickup passe in_progress (pas done) - l'evenement doit "
            "refleter une collecte, pas une finalisation",
        )


class ApiDriverDropoffCharacterizationTests(TestCase):
    def _make_order_with_laundry(self, phone):
        laundry = LaundryPartner.objects.create(name="Pressing Test", phone="0700007200", is_active=True)
        order = self._make_bare_order(phone)
        order.laundry_partner = laundry
        order.save(update_fields=["laundry_partner"])
        return order

    def _make_bare_order(self, phone):
        customer = Customer.objects.create(name="Client Test", phone=phone, address="Riviera 3")
        return Order.objects.create(customer=customer, status="in_progress")

    def test_dropoff_marks_pickup_leg_done_with_finished_at(self):
        driver = _make_driver("0700007104")
        order = self._make_order_with_laundry("0700007005")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="in_progress")

        resp = self.client.post(
            reverse("driver-dropoff", args=[order.id]),
            data={},
            **_driver_headers(driver),
        )
        self.assertEqual(resp.status_code, 200)

        leg = DeliveryLeg.objects.get(order=order, leg_type="pickup")
        self.assertEqual(leg.status, "done")
        self.assertIsNotNone(leg.finished_at)

    def test_dropoff_does_not_mark_order_done_when_return_leg_missing(self):
        driver = _make_driver("0700007105")
        order = self._make_order_with_laundry("0700007006")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="in_progress")

        self.client.post(
            reverse("driver-dropoff", args=[order.id]),
            data={},
            **_driver_headers(driver),
        )

        order.refresh_from_db()
        self.assertNotEqual(order.status, "done")

    def test_dropoff_refused_when_driver_not_assigned_to_pickup_leg(self):
        assigned_driver = _make_driver("0700007106")
        other_driver = _make_driver("0700007107")
        order = self._make_order_with_laundry("0700007007")
        DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=assigned_driver, status="in_progress")

        resp = self.client.post(
            reverse("driver-dropoff", args=[order.id]),
            data={},
            **_driver_headers(other_driver),
        )

        self.assertEqual(
            resp.status_code, 403,
            "un livreur non affecte a la jambe pickup ne doit pas pouvoir la finaliser",
        )
        leg = DeliveryLeg.objects.get(order=order, leg_type="pickup")
        self.assertEqual(leg.status, "in_progress")
        self.assertEqual(leg.driver_id, assigned_driver.id)
