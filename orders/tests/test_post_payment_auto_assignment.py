from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from orders.models import Customer, DeliveryLeg, Order, Payment
from orders.services import trigger_post_payment_auto_assignment
from partners.models import DeliveryPartner, LaundryPartner


def make_customer(phone="0700099101"):
    return Customer.objects.create(
        name="Client post paiement",
        phone=phone,
        latitude=Decimal("5.350000"),
        longitude=Decimal("-3.980000"),
    )


def make_order(customer):
    return Order.objects.create(
        customer=customer,
        status="pending",
        payment_status="pending",
        total_client_ttc=Decimal("10000"),
        total=Decimal("10000"),
        amount_paid=Decimal("0"),
        pickup_lat=Decimal("5.350000"),
        pickup_lng=Decimal("-3.980000"),
        delivery_lat=Decimal("5.350000"),
        delivery_lng=Decimal("-3.980000"),
        bag_size="small",
    )


class PostPaymentAutoAssignmentTests(TestCase):
    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=False)
    def test_flag_off_ne_declenche_rien(self):
        order = make_order(make_customer())

        result = trigger_post_payment_auto_assignment(order.id)

        self.assertFalse(result["triggered"])
        self.assertEqual(result["reason"], "flag_disabled")

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_commande_non_payee_ne_declenche_rien(self):
        order = make_order(make_customer("0700099102"))

        result = trigger_post_payment_auto_assignment(order.id)

        self.assertFalse(result["triggered"])
        self.assertEqual(result["reason"], "payment_not_confirmed")
        self.assertIsNone(
            Order.objects.get(pk=order.pk).laundry_partner_id
        )

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_commande_deja_affectee_n_est_pas_reaffectee(self):
        customer = make_customer("0700099103")

        laundry = LaundryPartner.objects.create(
            name="Pressing conservé",
            phone="0700099201",
            is_active=True,
            latitude=Decimal("5.351000"),
            longitude=Decimal("-3.981000"),
        )

        driver = DeliveryPartner.objects.create(
            name="Livreur conservé",
            phone="0700099301",
            email="livreur-conserve@example.com",
            is_active=True,
            latitude=Decimal("5.352000"),
            longitude=Decimal("-3.982000"),
        )

        order = make_order(customer)
        order.laundry_partner = laundry
        order.pickup_driver = driver
        order.save(
            update_fields=[
                "laundry_partner",
                "pickup_driver",
            ]
        )

        DeliveryLeg.objects.create(
            order=order,
            leg_type="pickup",
            driver=driver,
            status="assigned",
            driver_amount=Decimal("1000"),
        )

        Payment.objects.create(
            order=order,
            amount=Decimal("10000"),
            channel="test",
            reference=f"POSTPAY-{order.id}",
        )

        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")

        with patch(
            "orders.client_api._bc1_auto_assign_pickup_and_laundry"
        ) as helper:
            result = trigger_post_payment_auto_assignment(order.id)

        order.refresh_from_db()

        self.assertTrue(result["triggered"])
        self.assertEqual(result["reason"], "already_fully_assigned")
        self.assertEqual(helper.call_count, 0)
        self.assertEqual(order.laundry_partner_id, laundry.id)
        self.assertEqual(order.pickup_driver_id, driver.id)
        self.assertEqual(
            DeliveryLeg.objects.filter(
                order=order,
                leg_type="pickup",
            ).count(),
            1,
        )


class PostPaymentPartialAssignmentProtectionTests(TestCase):
    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_pressing_existant_ne_peut_pas_etre_remplace(self):
        customer = make_customer("0700099111")

        protected_laundry = LaundryPartner.objects.create(
            name="Pressing protégé",
            phone="0700099211",
            is_active=True,
            latitude=Decimal("5.351000"),
            longitude=Decimal("-3.981000"),
        )

        replacement_laundry = LaundryPartner.objects.create(
            name="Pressing remplacement",
            phone="0700099212",
            is_active=True,
            latitude=Decimal("5.352000"),
            longitude=Decimal("-3.982000"),
        )

        driver = DeliveryPartner.objects.create(
            name="Livreur disponible",
            phone="0700099311",
            email="livreur-partiel@example.com",
            is_active=True,
            latitude=Decimal("5.352000"),
            longitude=Decimal("-3.982000"),
        )

        order = make_order(customer)
        order.laundry_partner = protected_laundry
        order.save(update_fields=["laundry_partner"])

        Payment.objects.create(
            order=order,
            amount=Decimal("10000"),
            channel="test",
            reference=f"POSTPAY-PARTIAL-L-{order.id}",
        )

        with patch(
            "orders.assignment.pick_best_laundry"
        ) as pick_laundry, patch(
            "orders.assignment.pick_best_driver",
            return_value=(driver, "test"),
        ):
            trigger_post_payment_auto_assignment(order.id)

        pick_laundry.assert_not_called()
        order.refresh_from_db()

        self.assertEqual(
            order.laundry_partner_id,
            protected_laundry.id,
        )
        self.assertEqual(order.pickup_driver_id, driver.id)

    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_livreur_existant_ne_peut_pas_etre_remplace(self):
        customer = make_customer("0700099112")

        laundry = LaundryPartner.objects.create(
            name="Pressing disponible",
            phone="0700099221",
            is_active=True,
            latitude=Decimal("5.351000"),
            longitude=Decimal("-3.981000"),
        )

        protected_driver = DeliveryPartner.objects.create(
            name="Livreur protégé",
            phone="0700099321",
            email="livreur-protege@example.com",
            is_active=True,
            latitude=Decimal("5.352000"),
            longitude=Decimal("-3.982000"),
        )

        replacement_driver = DeliveryPartner.objects.create(
            name="Livreur remplacement",
            phone="0700099322",
            email="livreur-remplacement@example.com",
            is_active=True,
            latitude=Decimal("5.353000"),
            longitude=Decimal("-3.983000"),
        )

        order = make_order(customer)
        order.pickup_driver = protected_driver
        order.save(update_fields=["pickup_driver"])

        DeliveryLeg.objects.create(
            order=order,
            leg_type="pickup",
            driver=protected_driver,
            status="assigned",
            driver_amount=Decimal("1000"),
        )

        Payment.objects.create(
            order=order,
            amount=Decimal("10000"),
            channel="test",
            reference=f"POSTPAY-PARTIAL-D-{order.id}",
        )

        with patch(
            "orders.assignment.pick_best_laundry",
            return_value=(laundry, "test"),
        ), patch(
            "orders.assignment.pick_best_driver"
        ) as pick_driver:
            trigger_post_payment_auto_assignment(order.id)

        pick_driver.assert_not_called()
        order.refresh_from_db()
        pickup_leg = DeliveryLeg.objects.get(
            order=order,
            leg_type="pickup",
        )

        self.assertEqual(order.laundry_partner_id, laundry.id)
        self.assertEqual(
            order.pickup_driver_id,
            protected_driver.id,
        )
        self.assertEqual(
            pickup_leg.driver_id,
            protected_driver.id,
        )


class PostPaymentAutomaticHookTests(TestCase):
    @override_settings(AUTO_ASSIGN_ON_CLIENT_ORDER=True)
    def test_payment_save_programme_automatiquement_affectation(self):
        customer = make_customer("0700099113")
        order = make_order(customer)

        with patch(
            "orders.services.trigger_post_payment_auto_assignment"
        ) as trigger:
            with self.captureOnCommitCallbacks(execute=True):
                Payment.objects.create(
                    order=order,
                    amount=Decimal("10000"),
                    channel="test",
                    reference=f"POSTPAY-HOOK-{order.id}",
                )

        trigger.assert_called_once_with(order.id)
