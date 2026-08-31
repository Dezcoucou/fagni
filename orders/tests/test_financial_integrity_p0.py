import logging
from decimal import Decimal
from django.test import TestCase
from orders.models import Customer, Order, OrderItem, DeliveryLeg
from partners.models import DeliveryPartner, LaundryPartner
from wallets.models import Wallet, WalletTransaction
from orders.views import apply_order_payment

logging.basicConfig(level=logging.DEBUG)

class FinancialIntegrityP0Tests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Test Client", phone="0700000001")
        self.driver = DeliveryPartner.objects.create(name="Test Driver", phone="0700000002")
        self.laundry = LaundryPartner.objects.create(name="Test Laundry", phone="0700000003")
        self.wallet_driver, _ = Wallet.objects.get_or_create(delivery_partner=self.driver, defaults={'currency': 'XOF', 'balance': 0})

    def test_scenario_auto_payout_signal(self):
        """Test que le signal déclenche le payout automatiquement."""
        print("\n===== TEST SIGNAL AUTO-PAYOUT =====")
        order = Order.objects.create(
            customer=self.customer, laundry_partner=self.laundry,
            delivery_fee=Decimal("2000.00"), amount_driver_partner=Decimal("1200.00"), logistic_margin=800,
        )
        OrderItem.objects.create(order=order, designation="Test", quantity=1, unit_price=Decimal("5000.00"), total=Decimal("5000.00"))
        
        leg_pickup, _ = DeliveryLeg.objects.get_or_create(order=order, driver=self.driver, leg_type="pickup", defaults={'status': "assigned", 'distance_km': Decimal("10")})
        
        order.update_financials(save=True)
        order.recompute_logistics_from_legs(save_legs=True, save_order=True)
        
        # Paiement et forçage du statut paid (comme vu précédemment)
        apply_order_payment(order, Decimal("20000.00"), channel="cash", reference="TEST-A")
        order.refresh_from_db()
        if order.payment_status != "paid":
            order.payment_status = "paid"
            order.amount_paid = order.total_client_ttc or 7000
            order.save(update_fields=["payment_status", "amount_paid"])

        print(f"[AVANT SAVE] leg status={leg_pickup.status}, driver_amount={leg_pickup.driver_amount}")
        
        # C'est ici que la magie opère : le save() devrait déclencher le signal
        leg_pickup.status = "done"
        leg_pickup.save(update_fields=["status"])
        
        # On vérifie si le signal a fait son travail
        leg_pickup.refresh_from_db()
        print(f"[APRÈS SAVE] leg driver_amount={leg_pickup.driver_amount}")
        
        payouts = WalletTransaction.objects.filter(order=order, type="payout", direction="in")
        print(f"[CHECK SIGNAL] Payouts créés automatiquement: {payouts.count()}")
        
        if payouts.exists():
            print(f"✅ SUCCÈS DU SIGNAL : Montant payé = {payouts.first().amount}")
        else:
            print("❌ ÉCHEC DU SIGNAL : Aucun payout créé.")
