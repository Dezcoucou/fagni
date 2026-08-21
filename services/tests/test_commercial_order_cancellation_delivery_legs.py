from decimal import Decimal

from django.test import TestCase

from orders.models import Customer, DeliveryLeg, Order
from partners.models import DeliveryPartner, LaundryPartner
from services.cancellation import cancel_commercial_order
from wallets.models import WalletTransaction
from wallets.services import get_or_create_wallet_for_delivery_partner


class CommercialOrderCancellationDeliveryLegTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client Cancel DeliveryLeg",
            phone="0700000199",
        )

        self.driver = DeliveryPartner.objects.create(
            name="Driver Cancel DeliveryLeg",
            phone="0700000188",
        )

        self.laundry = LaundryPartner.objects.create(
            name="Laundry Cancel DeliveryLeg",
            phone="0700000177",
        )

    def _make_order(self, *, payment_status="unpaid"):
        order = Order.objects.create(
            customer=self.customer,
            laundry_partner=self.laundry,
            status="pending",
            total_client_ttc=Decimal("5000"),
            amount_paid=(
                Decimal("5000")
                if payment_status == "paid"
                else Decimal("0")
            ),
            payment_status=payment_status,
            notes="Historique commande A6.2",
        )

        # Certains flux Order peuvent créer/synchroniser des legs.
        # Le test repart volontairement d'un état contrôlé.
        DeliveryLeg.objects.filter(order=order).delete()

        return order

    def _make_leg(
        self,
        *,
        order,
        leg_type="pickup",
        status="pending",
        client_fee_share="1000",
        driver_amount="700",
        fagni_margin="300",
    ):
        return DeliveryLeg.objects.create(
            order=order,
            driver=self.driver,
            leg_type=leg_type,
            status=status,
            client_fee_share=Decimal(client_fee_share),
            driver_amount=Decimal(driver_amount),
            fagni_margin=Decimal(fagni_margin),
        )

    def test_pending_leg_is_canceled_and_finance_zeroed(self):
        order = self._make_order()

        leg = self._make_leg(
            order=order,
            status="pending",
        )

        result = cancel_commercial_order(
            order=order,
            reason="Annulation client",
        )

        leg.refresh_from_db()
        order.refresh_from_db()

        self.assertEqual(order.status, "canceled")
        self.assertEqual(leg.status, "canceled")
        self.assertEqual(leg.client_fee_share, Decimal("0"))
        self.assertEqual(leg.driver_amount, Decimal("0"))
        self.assertEqual(leg.fagni_margin, Decimal("0"))
        self.assertEqual(result["delivery_legs_canceled"], 1)

    def test_assigned_leg_is_canceled_and_finance_zeroed(self):
        order = self._make_order()

        leg = self._make_leg(
            order=order,
            status="assigned",
        )

        result = cancel_commercial_order(
            order=order,
            reason="Annulation avant collecte",
        )

        leg.refresh_from_db()

        self.assertEqual(leg.status, "canceled")
        self.assertEqual(leg.client_fee_share, Decimal("0"))
        self.assertEqual(leg.driver_amount, Decimal("0"))
        self.assertEqual(leg.fagni_margin, Decimal("0"))
        self.assertEqual(result["delivery_legs_canceled"], 1)

    def test_in_progress_leg_is_canceled_and_finance_zeroed(self):
        order = self._make_order()

        leg = self._make_leg(
            order=order,
            status="in_progress",
        )

        result = cancel_commercial_order(
            order=order,
            reason="Annulation opérationnelle",
        )

        leg.refresh_from_db()

        self.assertEqual(leg.status, "canceled")
        self.assertEqual(leg.client_fee_share, Decimal("0"))
        self.assertEqual(leg.driver_amount, Decimal("0"))
        self.assertEqual(leg.fagni_margin, Decimal("0"))
        self.assertEqual(result["delivery_legs_canceled"], 1)

    def test_done_leg_is_preserved(self):
        order = self._make_order()

        leg = self._make_leg(
            order=order,
            status="assigned",
        )

        # Passage forcé en done sans déclencher le mécanisme de payout :
        # on veut ici tester uniquement la protection historique DONE.
        DeliveryLeg.objects.filter(pk=leg.pk).update(
            status="done",
        )

        leg.refresh_from_db()

        frozen_client_fee = leg.client_fee_share
        frozen_driver_amount = leg.driver_amount
        frozen_margin = leg.fagni_margin

        result = cancel_commercial_order(
            order=order,
            reason="Annulation après historique logistique",
        )

        leg.refresh_from_db()

        self.assertEqual(leg.status, "done")
        self.assertEqual(
            leg.client_fee_share,
            frozen_client_fee,
        )
        self.assertEqual(
            leg.driver_amount,
            frozen_driver_amount,
        )
        self.assertEqual(
            leg.fagni_margin,
            frozen_margin,
        )
        self.assertEqual(result["delivery_legs_canceled"], 0)

    def test_paid_leg_is_preserved_even_if_status_was_corrupted(self):
        order = self._make_order(
            payment_status="paid",
        )

        leg = self._make_leg(
            order=order,
            status="assigned",
            client_fee_share="1000",
            driver_amount="700",
            fagni_margin="300",
        )

        # Création normale du payout via passage à DONE.
        with self.captureOnCommitCallbacks(execute=True):
            leg.status = "done"
            leg.save(update_fields=["status"])

        payout = WalletTransaction.objects.filter(
            order=order,
            leg=leg,
            type="payout",
            direction="in",
        ).first()

        self.assertIsNotNone(payout)

        leg.refresh_from_db()

        frozen_client_fee = leg.client_fee_share
        frozen_driver_amount = leg.driver_amount
        frozen_margin = leg.fagni_margin

        # Simulation d'une corruption SQL/historique :
        # payout existe mais statut n'est plus DONE.
        DeliveryLeg.objects.filter(pk=leg.pk).update(
            status="pending",
        )

        leg.refresh_from_db()
        self.assertEqual(leg.status, "pending")

        result = cancel_commercial_order(
            order=order,
            reason="Annulation avec historique payout",
        )

        leg.refresh_from_db()

        # A6.1 doit respecter le verrou financier :
        # aucun passage à canceled, aucun montant neutralisé.
        self.assertEqual(leg.status, "pending")
        self.assertEqual(
            leg.client_fee_share,
            frozen_client_fee,
        )
        self.assertEqual(
            leg.driver_amount,
            frozen_driver_amount,
        )
        self.assertEqual(
            leg.fagni_margin,
            frozen_margin,
        )

        self.assertEqual(
            WalletTransaction.objects.filter(
                order=order,
                leg=leg,
                type="payout",
                direction="in",
            ).count(),
            1,
        )

        self.assertEqual(result["delivery_legs_canceled"], 0)

    def test_already_canceled_leg_is_idempotently_preserved(self):
        order = self._make_order()

        leg = self._make_leg(
            order=order,
            status="canceled",
            client_fee_share="0",
            driver_amount="0",
            fagni_margin="0",
        )

        result = cancel_commercial_order(
            order=order,
            reason="Retry annulation",
        )

        leg.refresh_from_db()

        self.assertEqual(leg.status, "canceled")
        self.assertEqual(leg.client_fee_share, Decimal("0"))
        self.assertEqual(leg.driver_amount, Decimal("0"))
        self.assertEqual(leg.fagni_margin, Decimal("0"))
        self.assertEqual(result["delivery_legs_canceled"], 0)

    def test_cancellation_does_not_create_driver_payout(self):
        order = self._make_order(
            payment_status="paid",
        )

        leg = self._make_leg(
            order=order,
            status="assigned",
            driver_amount="700",
        )

        self.assertFalse(
            WalletTransaction.objects.filter(
                order=order,
                leg=leg,
                type="payout",
                direction="in",
            ).exists()
        )

        result = cancel_commercial_order(
            order=order,
            reason="Annulation avant fin de course",
        )

        leg.refresh_from_db()

        self.assertEqual(leg.status, "canceled")
        self.assertEqual(result["delivery_legs_canceled"], 1)

        self.assertFalse(
            WalletTransaction.objects.filter(
                order=order,
                leg=leg,
                type="payout",
                direction="in",
            ).exists()
        )

    def test_mixed_delivery_legs_count_only_actually_canceled_legs(self):
        order = self._make_order()

        pickup = self._make_leg(
            order=order,
            leg_type="pickup",
            status="assigned",
        )

        return_leg = self._make_leg(
            order=order,
            leg_type="return",
            status="pending",
        )

        result = cancel_commercial_order(
            order=order,
            reason="Annulation complète",
        )

        pickup.refresh_from_db()
        return_leg.refresh_from_db()

        self.assertEqual(pickup.status, "canceled")
        self.assertEqual(return_leg.status, "canceled")
        self.assertEqual(result["delivery_legs_canceled"], 2)

    def test_second_commercial_cancellation_reports_zero_delivery_legs(self):
        order = self._make_order()

        leg = self._make_leg(
            order=order,
            status="assigned",
        )

        first_result = cancel_commercial_order(
            order=order,
            reason="Première annulation",
        )

        self.assertEqual(
            first_result["delivery_legs_canceled"],
            1,
        )

        leg.refresh_from_db()
        self.assertEqual(leg.status, "canceled")

        second_result = cancel_commercial_order(
            order=order,
            reason="Retry API",
        )

        self.assertTrue(second_result["already_canceled"])
        self.assertEqual(
            second_result["delivery_legs_canceled"],
            0,
        )
