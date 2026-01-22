from decimal import Decimal
from django.test import TestCase
from orders.models import Customer, Order, DeliveryLeg
from partners.models import DeliveryPartner

class DeliveryLegPayoutTests(TestCase):
    def test_leg_done_triggers_payout_when_order_paid_idempotent(self):
        c = Customer.objects.create(name="C", phone="0700000999")
        driver = DeliveryPartner.objects.create(name="Driver", phone="0700000888")

        o = Order.objects.create(
            customer=c,
            total_client_ttc=Decimal("1000"),
            amount_paid=Decimal("1000"),
            payment_status="paid",
        )

        leg = DeliveryLeg.objects.create(
            order=o,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            driver_amount=Decimal("300"),
        )

        # transition -> done : doit tenter payout (idempotent)
        with self.captureOnCommitCallbacks(execute=True):
            leg.status = "done"
            leg.save()

        # relance save done (ne doit pas dupliquer)
        with self.captureOnCommitCallbacks(execute=True):
            leg.save()

        # On vérifie juste que la commande a au moins une tx payout driver liée à leg
        from wallets.models import WalletTransaction
        qs = WalletTransaction.objects.filter(order=o, leg=leg)
        self.assertTrue(qs.exists())
        self.assertEqual(qs.count(), 1)
