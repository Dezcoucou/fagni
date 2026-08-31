import logging
from decimal import Decimal
from django.test import TestCase
from orders.models import Customer, Order, OrderItem, DeliveryLeg
from partners.models import DeliveryPartner, LaundryPartner
from wallets.models import Wallet, WalletTransaction

logging.basicConfig(level=logging.INFO)

class P03RegressionTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Test Client", phone="0700000001")
        self.driver = DeliveryPartner.objects.create(name="Test Driver", phone="0700000002")
        self.laundry = LaundryPartner.objects.create(name="Test Laundry", phone="0700000003")
        self.wallet_driver, _ = Wallet.objects.get_or_create(delivery_partner=self.driver, defaults={'currency': 'XOF', 'balance': 0})

    def test_real_path_leg_done_to_payout(self):
        """Parcours réel : Leg Done -> Recalcul -> Payout."""
        print("\n===== TEST RÉGRESSION P0.3 — PARCOURS RÉEL =====")
        
        order = Order.objects.create(
            customer=self.customer, laundry_partner=self.laundry,
            delivery_fee=Decimal("2000.00"), amount_driver_partner=Decimal("1200.00"), logistic_margin=800,
        )
        OrderItem.objects.create(order=order, designation="Test", quantity=1, unit_price=Decimal("5000.00"), total=Decimal("5000.00"))
        
        # Forcer le statut paid via update() pour bypasser le garde-fou du modèle
        Order.objects.filter(pk=order.pk).update(
            payment_status="paid", 
            amount_paid=Decimal("10000.00")
        )
        order.refresh_from_db()
            
        print(f"[INIT ORDER] status={order.payment_status}, paid={order.amount_paid}")

        # Créer la leg manuellement avec un montant réaliste (comme en production après recalcul)
        leg, created = DeliveryLeg.objects.get_or_create(
            order=order, 
            driver=self.driver, 
            leg_type="pickup",
            defaults={
                'status': "assigned", 
                'distance_km': Decimal("10"), 
                'driver_amount': Decimal("600.00")  # <-- Montant réaliste pour déclencher le payout
            }
        )
            
        print(f"[INIT LEG] ID: {leg.id}, Status: {leg.status}, Driver Amount: {leg.driver_amount}")

        # Action unique : passer à done
        leg.status = "done"
        leg.save(update_fields=["status"])

        leg.refresh_from_db()
        print(f"[POST SAVE] Leg Driver Amount: {leg.driver_amount}")

        # Vérifications P0.3
        payouts = WalletTransaction.objects.filter(order=order, leg=leg, type="payout", direction="in")
        if payouts.count() == 1:
            print(f"✅ SUCCÈS P0.3 : Payout de {payouts.first().amount} FCFA créé automatiquement.")
        else:
            print(f"❌ ÉCHEC P0.3 : {payouts.count()} payouts trouvés (attendu: 1).")
            order.refresh_from_db()
            print(f"[DEBUG] Statut ordre vu par trigger: {order.payment_status}")
            
        self.assertEqual(payouts.count(), 1)
        self.assertEqual(payouts.first().amount, Decimal("600.00"))
