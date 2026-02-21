# orders/tests/test_leg_status_transitions.py

from django.test import TestCase
from django.utils import timezone

from orders.models import Customer, Order, DeliveryLeg
from orders.views import update_leg_status
from partners.models import DeliveryPartner


class LegStatusTransitionsTests(TestCase):
    def _make_driver(self):
        return DeliveryPartner.objects.create(
            name="Driver Test",
            phone="0700000001",
        )

    def _make_customer(self):
        return Customer.objects.create(
            name="Client Test",
            phone="0100000001",
            address="Abidjan",
        )

    def _make_order_with_legs(self):
        customer = self._make_customer()
        driver = self._make_driver()

        # important: sinon guard DeliveryLeg.save empêche assigned/in_progress/done
        o = Order.objects.create(
            customer=customer,
            status="pending",
            delivery_partner=driver,
        )

        pickup = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="pending",
        )
        ret = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="return",
            status="pending",
        )
        return o, driver, pickup, ret

    # -------------------------
    # ACCEPT (pickup)
    # -------------------------
    def test_accept_from_pending_moves_to_assigned(self):
        o, driver, pickup, ret = self._make_order_with_legs()
        self.assertEqual(pickup.status, "pending")

        changed, msg = update_leg_status(pickup, "accept", user=None)
        pickup.refresh_from_db()
        ret.refresh_from_db()
        o.refresh_from_db()

        self.assertTrue(changed)
        self.assertIn("pending", msg)
        self.assertEqual(pickup.status, "assigned")

    def test_accept_on_done_is_idempotent(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        pickup.status = "done"
        pickup.save(update_fields=["status"])

        changed, msg = update_leg_status(pickup, "accept", user=None)
        pickup.refresh_from_db()

        self.assertTrue(changed)
        self.assertIn("déjà terminée", msg)
        self.assertEqual(pickup.status, "done")

    def test_accept_on_in_progress_does_not_downgrade(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        pickup.status = "in_progress"
        pickup.started_at = timezone.now()
        pickup.save(update_fields=["status", "started_at"])

        changed, msg = update_leg_status(pickup, "accept", user=None)
        pickup.refresh_from_db()

        # pas de downgrade
        self.assertEqual(pickup.status, "in_progress")
        self.assertTrue(
            ("déjà" in (msg or "").lower()) or ("aucune transition" in (msg or "").lower())
        )

    # -------------------------
    # START (pickup)
    # -------------------------
    def test_start_from_pending_is_refused(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        changed, msg = update_leg_status(pickup, "start", user=None)
        pickup.refresh_from_db()

        self.assertFalse(changed)
        self.assertIn("d'abord accepter", (msg or "").lower())
        self.assertEqual(pickup.status, "pending")

    def test_start_from_assigned_moves_to_in_progress_and_sets_started_at(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        changed, msg = update_leg_status(pickup, "accept", user=None)
        pickup.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(pickup.status, "assigned")

        changed, msg = update_leg_status(pickup, "start", user=None)
        pickup.refresh_from_db()
        o.refresh_from_db()

        self.assertTrue(changed)
        self.assertEqual(pickup.status, "in_progress")
        self.assertIsNotNone(pickup.started_at)

    def test_start_on_in_progress_is_idempotent(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        pickup.status = "in_progress"
        pickup.started_at = timezone.now()
        pickup.save(update_fields=["status", "started_at"])

        changed, msg = update_leg_status(pickup, "start", user=None)
        pickup.refresh_from_db()

        self.assertTrue(changed)
        self.assertIn("déjà démarrée", msg)
        self.assertEqual(pickup.status, "in_progress")

    # -------------------------
    # FINISH (pickup)
    # -------------------------
    def test_finish_from_assigned_moves_to_done_and_sets_finished_at(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        changed, msg = update_leg_status(pickup, "accept", user=None)
        pickup.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(pickup.status, "assigned")

        changed, msg = update_leg_status(pickup, "finish", user=None)
        pickup.refresh_from_db()
        o.refresh_from_db()

        self.assertTrue(changed)
        self.assertEqual(pickup.status, "done")
        self.assertIsNotNone(pickup.finished_at)

    def test_finish_from_in_progress_moves_to_done(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        pickup.status = "in_progress"
        pickup.started_at = timezone.now()
        pickup.save(update_fields=["status", "started_at"])

        changed, msg = update_leg_status(pickup, "finish", user=None)
        pickup.refresh_from_db()

        self.assertTrue(changed)
        self.assertEqual(pickup.status, "done")
        self.assertIsNotNone(pickup.finished_at)

    def test_finish_on_done_is_idempotent(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        pickup.status = "done"
        pickup.finished_at = timezone.now()
        pickup.save(update_fields=["status", "finished_at"])

        changed, msg = update_leg_status(pickup, "finish", user=None)
        pickup.refresh_from_db()

        self.assertTrue(changed)
        self.assertIn("déjà terminée", msg)
        self.assertEqual(pickup.status, "done")

    # -------------------------
    # RETURN: verrou pickup_done (accept/start/finish)
    # -------------------------
    def test_return_accept_is_refused_if_pickup_not_done(self):
        o, driver, pickup, ret = self._make_order_with_legs()
        self.assertEqual(pickup.status, "pending")
        self.assertEqual(ret.status, "pending")

        changed, msg = update_leg_status(ret, "accept", user=None)
        ret.refresh_from_db()

        self.assertFalse(changed)
        self.assertIn("pickup", (msg or "").lower())
        self.assertIn("termin", (msg or "").lower())  # "terminé"

    def test_return_start_is_refused_if_pickup_not_done_even_if_wash_ready(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        # on bypass le garde-fou "linge prêt" pour tester UNIQUEMENT la règle pickup_done
        o.wash_complete_time = timezone.now()
        o.save(update_fields=["wash_complete_time"])

        changed, msg = update_leg_status(ret, "start", user=None)
        ret.refresh_from_db()

        self.assertFalse(changed)
        self.assertIn("pickup", (msg or "").lower())
        self.assertIn("termin", (msg or "").lower())

    def test_return_finish_is_refused_if_pickup_not_done_even_if_wash_ready(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        o.wash_complete_time = timezone.now()
        o.save(update_fields=["wash_complete_time"])

        changed, msg = update_leg_status(ret, "finish", user=None)
        ret.refresh_from_db()

        self.assertFalse(changed)
        self.assertIn("pickup", (msg or "").lower())
        self.assertIn("termin", (msg or "").lower())

    # -------------------------
    # RETURN: verrou wash_complete_time (start/finish)
    # -------------------------
    def test_return_start_is_refused_if_wash_not_ready_even_if_pickup_done(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        # pickup DONE
        pickup.status = "done"
        pickup.save(update_fields=["status"])

        # wash_complete_time ABSENT => start/finish return interdits
        o.wash_complete_time = None
        o.save(update_fields=["wash_complete_time"])

        changed, msg = update_leg_status(ret, "start", user=None)
        ret.refresh_from_db()

        self.assertFalse(changed)
        self.assertIn("linge", (msg or "").lower())
        self.assertIn("prêt", (msg or "").lower())

    def test_return_finish_is_refused_if_wash_not_ready_even_if_pickup_done(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        pickup.status = "done"
        pickup.save(update_fields=["status"])

        o.wash_complete_time = None
        o.save(update_fields=["wash_complete_time"])

        changed, msg = update_leg_status(ret, "finish", user=None)
        ret.refresh_from_db()

        self.assertFalse(changed)
        self.assertIn("linge", (msg or "").lower())
        self.assertIn("prêt", (msg or "").lower())

    # -------------------------
    # CANCEL: pickup cancel interdit si return déjà démarré
    # -------------------------
    def test_cancel_pickup_is_refused_if_return_started(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        # pickup doit être "cancelable"
        changed, msg = update_leg_status(pickup, "accept", user=None)
        pickup.refresh_from_db()
        self.assertEqual(pickup.status, "assigned")

        # return "started" au sens métier (assigned / in_progress / done)
        ret.status = "assigned"
        ret.save(update_fields=["status"])

        changed, msg = update_leg_status(pickup, "cancel", user=None)
        pickup.refresh_from_db()

        self.assertFalse(changed)
        self.assertIn("annulation", (msg or "").lower())
        self.assertIn("retour", (msg or "").lower())

    def test_finish_pickup_is_refused_if_return_active(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        changed, msg = update_leg_status(pickup, "accept", user=None)
        pickup.refresh_from_db()
        self.assertEqual(pickup.status, "assigned")

        # return actif (pas pending/canceled)
        ret.status = "assigned"
        ret.save(update_fields=["status"])

        changed, msg = update_leg_status(pickup, "finish", user=None)
        pickup.refresh_from_db()

        self.assertFalse(changed)
        self.assertIn("retour", (msg or "").lower())

    def test_cancel_pickup_is_allowed_if_return_not_started(self):
        o, driver, pickup, ret = self._make_order_with_legs()

        changed, msg = update_leg_status(pickup, "accept", user=None)
        pickup.refresh_from_db()
        self.assertEqual(pickup.status, "assigned")

        # return reste pending
        ret.refresh_from_db()
        self.assertEqual(ret.status, "pending")

        changed, msg = update_leg_status(pickup, "cancel", user=None)
        pickup.refresh_from_db()

        self.assertTrue(changed)
        self.assertEqual(pickup.status, "canceled")
