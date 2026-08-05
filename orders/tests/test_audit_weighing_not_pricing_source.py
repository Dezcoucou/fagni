"""
Audit parcours production/blanchisserie V1 - Etape 2 : test de
NON-REGRESSION (pas une caracterisation de bug). Objectif : prouver que le
poids (weight_kg / final_weight_kg) n'est jamais une source de calcul
financier. Aucun calcul financier ni pricing n'est modifie ou teste a
partir du poids ici - on verifie uniquement l'ABSENCE d'effet.

Tous les tests de ce fichier doivent deja etre verts : c'est un filet de
securite pour l'Etape 3, pas une liste d'anomalies a corriger.
"""
import inspect
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from orders import views as orders_views
from orders.models import Customer, DeliveryLeg, Order, OrderWeighing
from partners.models import DeliveryPartner

User = get_user_model()


def _make_customer(phone):
    return Customer.objects.create(name="Client Audit", phone=phone, address="Riviera 3")


def _make_driver(phone, email):
    return DeliveryPartner.objects.create(name="Livreur Audit", phone=phone, email=email, is_active=True)


def _make_locked_order(phone, driver):
    order = Order.objects.create(
        customer=_make_customer(phone), status="in_progress",
        total_client_ttc=Decimal("7500"),
    )
    DeliveryLeg.objects.create(order=order, leg_type="pickup", driver=driver, status="in_progress")
    return order


class WeighingIsNotAPricingSourceTests(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def test_driver_weighing_submission_never_changes_total_client_ttc(self):
        driver = _make_driver("0700060101", "d1@example.com")
        user = User.objects.create_user(username="d1", email=driver.email, password="x")
        order = _make_locked_order("0700060001", driver)
        locked_price = order.total_client_ttc

        self.client.force_login(user)
        self.client.post(reverse("orders:driver_weighing", args=[order.id]), data={"weight_kg": "12.75"})

        order.refresh_from_db()
        self.assertEqual(order.total_client_ttc, locked_price)

    def test_ops_resolution_never_changes_total_client_ttc(self):
        driver = _make_driver("0700060102", "d2@example.com")
        order = _make_locked_order("0700060002", driver)
        locked_price = order.total_client_ttc
        ow = OrderWeighing.objects.create(order=order, status="disputed", weight_kg=Decimal("5.0"))
        staff = User.objects.create_user(username="staff_pricing", email="staff_pricing@example.com", password="x", is_staff=True)

        self.client.force_login(staff)
        self.client.post(
            reverse("orders:ops_weighing_resolve", args=[order.id]),
            data={"final_weight_kg": "99.9", "resolution_notes": "poids tres different", "address": "Riviera 3"},
        )

        order.refresh_from_db()
        ow.refresh_from_db()
        self.assertEqual(order.total_client_ttc, locked_price)

    def test_no_weighing_function_calls_update_financials(self):
        weighing_functions = [
            orders_views.driver_weighing,
            orders_views.laundry_weighing,
            orders_views.laundry_weighing_confirm,
            orders_views.laundry_weighing_dispute,
            orders_views.ops_weighing_resolve,
        ]
        for fn in weighing_functions:
            source = inspect.getsource(fn)
            self.assertNotIn(
                "update_financials", source,
                f"{fn.__name__} ne doit jamais appeler Order.update_financials()",
            )
            self.assertNotIn(
                "compute_totals", source,
                f"{fn.__name__} ne doit jamais recalculer les totaux",
            )

    def test_price_remains_the_one_already_locked_on_order_end_to_end(self):
        driver = _make_driver("0700060103", "d3@example.com")
        driver_user = User.objects.create_user(username="d3", email=driver.email, password="x")
        laundry_owner_email = "own_pricing@example.com"
        order = _make_locked_order("0700060003", driver)
        locked_price = order.total_client_ttc

        self.client.force_login(driver_user)
        self.client.post(reverse("orders:driver_weighing", args=[order.id]), data={"weight_kg": "6.4"})
        order.refresh_from_db()
        self.assertEqual(order.total_client_ttc, locked_price, "apres pesee livreur")

        from partners.models import LaundryPartner
        laundry = LaundryPartner.objects.create(name="Pressing Pricing", phone="0700060199", email=laundry_owner_email, is_active=True)
        order.laundry_partner = laundry
        order.save(update_fields=["laundry_partner"])
        laundry_user = User.objects.create_user(username="lp_pricing", email=laundry_owner_email, password="x")

        self.client.force_login(laundry_user)
        self.client.post(reverse("orders:laundry_weighing_dispute", args=[order.id]), data={"reason": "ecart de poids"})
        order.refresh_from_db()
        self.assertEqual(order.total_client_ttc, locked_price, "apres contestation blanchisserie")

        staff = User.objects.create_user(username="ops_pricing", email="ops_pricing@example.com", password="x", is_staff=True)
        self.client.force_login(staff)
        self.client.post(
            reverse("orders:ops_weighing_resolve", args=[order.id]),
            data={"final_weight_kg": "6.9", "resolution_notes": "resolu", "address": "Riviera 3"},
        )
        order.refresh_from_db()
        self.assertEqual(order.total_client_ttc, locked_price, "apres resolution OPS")
