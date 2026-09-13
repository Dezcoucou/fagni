"""
Contrat P0 DeliveryLeg — règle métier Phase 14.

Règle cible testée : ``assigned`` signifie qu'un acteur a explicitement
accepté sa mission. Une affectation OPS/BC3 renseigne l'acteur, mais laisse
la jambe ``pending``. Ces tests décrivent volontairement le contrat cible,
indépendamment de certains comportements legacy actuels.
"""

from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone

from orders.models import Customer, DeliveryLeg, Order, sync_delivery_legs_for_order
from orders.service_layer.legs import normalize_order_legs as normalize_leg_statuses
from orders.views import update_leg_status
from partners.models import DeliveryPartner


class P0DeliveryLegBusinessContractTests(TransactionTestCase):
    """Contrat métier professionnel : pickup=A, return=B."""

    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client contrat P0",
            phone="0700001400",
            address="Riviera 3",
        )
        self.pickup_driver = DeliveryPartner.objects.create(
            name="Driver pickup A",
            phone="0700001401",
            is_active=True,
        )
        self.return_driver = DeliveryPartner.objects.create(
            name="Driver return B",
            phone="0700001402",
            is_active=True,
        )

    def _make_order_with_two_drivers(self, *, wash_ready=False):
        order = Order.objects.create(
            customer=self.customer,
            status="pending",
            payment_status="unpaid",
            total_client_ttc=5000,
            pickup_driver=self.pickup_driver,
            delivery_partner=self.return_driver,
        )
        pickup = DeliveryLeg.objects.create(
            order=order,
            leg_type="pickup",
            driver=self.pickup_driver,
            status="pending",
        )
        # Construire les deux legs avant de rendre le linge disponible :
        # un save de DeliveryLeg avec wash_complete_time deja renseigne peut
        # declencher le sync qui cree lui-meme le return.
        return_leg, _created = DeliveryLeg.objects.get_or_create(
            order=order,
            leg_type="return",
            defaults={
                "driver": self.return_driver,
                "status": "pending",
            },
        )
        if return_leg.driver_id != self.return_driver.id:
            return_leg.driver = self.return_driver
            return_leg.status = "pending"
            return_leg.save(update_fields=["driver", "status"])

        if wash_ready:
            order.wash_complete_time = timezone.now()
            order.save(update_fields=["wash_complete_time"])
        return order, pickup, return_leg

    def _mark_pickup_done(self, pickup):
        pickup.status = "done"
        pickup.finished_at = timezone.now()
        pickup.save(update_fields=["status", "finished_at"])
        pickup.refresh_from_db()

    def test_p0_two_professional_assignments_keep_pickup_a_and_return_b_distinct(self):
        _order, pickup, return_leg = self._make_order_with_two_drivers()

        self.assertEqual(pickup.driver_id, self.pickup_driver.id)
        self.assertEqual(return_leg.driver_id, self.return_driver.id)
        self.assertNotEqual(pickup.driver_id, return_leg.driver_id)
        self.assertEqual(pickup.status, "pending")
        self.assertEqual(return_leg.status, "pending")

    def test_p0_pickup_assignment_is_pending_until_driver_a_explicitly_accepts(self):
        _order, pickup, _return_leg = self._make_order_with_two_drivers()

        self.assertEqual(pickup.status, "pending")
        changed, _message = update_leg_status(pickup, "accept")
        pickup.refresh_from_db()

        self.assertTrue(changed)
        self.assertEqual(pickup.driver_id, self.pickup_driver.id)
        self.assertEqual(pickup.status, "assigned")

    def test_p0_accepting_pickup_a_never_modifies_return_b(self):
        _order, pickup, return_leg = self._make_order_with_two_drivers()

        changed, _message = update_leg_status(pickup, "accept")
        pickup.refresh_from_db()
        return_leg.refresh_from_db()

        self.assertTrue(changed)
        self.assertEqual(pickup.status, "assigned")
        self.assertEqual(pickup.driver_id, self.pickup_driver.id)
        self.assertEqual(return_leg.status, "pending")
        self.assertEqual(return_leg.driver_id, self.return_driver.id)

    def test_p0_pickup_start_is_refused_while_pending_and_allowed_after_acceptance(self):
        _order, pickup, _return_leg = self._make_order_with_two_drivers()

        changed, _message = update_leg_status(pickup, "start")
        pickup.refresh_from_db()
        self.assertFalse(changed)
        self.assertEqual(pickup.status, "pending")

        accepted, _message = update_leg_status(pickup, "accept")
        pickup.refresh_from_db()
        started, _message = update_leg_status(pickup, "start")
        pickup.refresh_from_db()

        self.assertTrue(accepted)
        self.assertTrue(started)
        self.assertEqual(pickup.status, "in_progress")
        self.assertIsNotNone(pickup.started_at)

    def test_p0_finishing_pickup_a_keeps_return_b_pending(self):
        _order, pickup, return_leg = self._make_order_with_two_drivers(wash_ready=True)

        accepted, _message = update_leg_status(pickup, "accept")
        pickup.refresh_from_db()
        finished, _message = update_leg_status(pickup, "finish")
        pickup.refresh_from_db()
        return_leg.refresh_from_db()

        self.assertTrue(accepted)
        self.assertTrue(finished)
        self.assertEqual(pickup.status, "done")
        self.assertEqual(return_leg.driver_id, self.return_driver.id)
        self.assertEqual(return_leg.status, "pending")

    def test_p0_return_b_rejects_accept_start_and_finish_before_pickup_done(self):
        order, _pickup, return_leg = self._make_order_with_two_drivers(wash_ready=True)

        for action in ("accept", "start", "finish"):
            changed, _message = update_leg_status(return_leg, action)
            return_leg.refresh_from_db()
            self.assertFalse(changed, action)
            self.assertEqual(return_leg.status, "pending", action)
            self.assertEqual(return_leg.driver_id, self.return_driver.id, action)

        order.refresh_from_db()
        self.assertIsNotNone(order.wash_complete_time)

    def test_p0_return_b_rejects_start_and_finish_without_wash_complete_time(self):
        _order, pickup, return_leg = self._make_order_with_two_drivers()
        self._mark_pickup_done(pickup)

        # Etat construit pour isoler les gardes start/finish : B a accepté,
        # mais le pressing n'a pas encore rendu le linge disponible.
        return_leg.status = "assigned"
        return_leg.save(update_fields=["status"])

        for action in ("start", "finish"):
            changed, _message = update_leg_status(return_leg, action)
            return_leg.refresh_from_db()
            self.assertFalse(changed, action)
            self.assertEqual(return_leg.status, "assigned", action)
            self.assertIsNone(return_leg.started_at, action)

    def test_p0_return_b_waits_for_explicit_acceptance_after_pickup_done_and_wash_ready(self):
        order, pickup, return_leg = self._make_order_with_two_drivers(wash_ready=True)
        self._mark_pickup_done(pickup)
        return_leg.refresh_from_db()

        self.assertEqual(return_leg.driver_id, self.return_driver.id)
        self.assertEqual(return_leg.status, "pending")

        accepted, _message = update_leg_status(return_leg, "accept")
        return_leg.refresh_from_db()
        started, _message = update_leg_status(return_leg, "start")
        return_leg.refresh_from_db()

        self.assertTrue(accepted)
        self.assertTrue(started)
        self.assertEqual(return_leg.driver_id, self.return_driver.id)
        self.assertEqual(return_leg.status, "in_progress")
        self.assertEqual(pickup.driver_id, self.pickup_driver.id)
        order.refresh_from_db()
        self.assertIsNotNone(order.wash_complete_time)

    def test_p0_syncs_do_not_promote_return_b_from_pending_without_explicit_acceptance(self):
        order, pickup, return_leg = self._make_order_with_two_drivers(wash_ready=True)
        self._mark_pickup_done(pickup)

        sync_delivery_legs_for_order(order)
        normalize_leg_statuses(order, save=True)
        return_leg.refresh_from_db()

        self.assertEqual(return_leg.driver_id, self.return_driver.id)
        self.assertEqual(return_leg.status, "pending")

    def test_p0_post_save_on_commit_preserves_two_driver_assignment_and_pending_return(self):
        order, pickup, return_leg = self._make_order_with_two_drivers(wash_ready=True)

        # TransactionTestCase garantit qu'a la sortie du bloc les callbacks
        # transaction.on_commit des signaux sont effectivement exécutés.
        with transaction.atomic():
            pickup.status = "done"
            pickup.finished_at = timezone.now()
            pickup.save(update_fields=["status", "finished_at"])

        order.refresh_from_db()
        pickup.refresh_from_db()
        return_leg.refresh_from_db()

        self.assertEqual(pickup.status, "done")
        self.assertEqual(pickup.driver_id, self.pickup_driver.id)
        self.assertEqual(return_leg.driver_id, self.return_driver.id)
        self.assertEqual(return_leg.status, "pending")
        self.assertNotEqual(pickup.driver_id, return_leg.driver_id)
