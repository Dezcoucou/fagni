from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from orders.models import Customer, Order, OrderItem
from mlm.models import ReferralLink, ReferralCommission, WalletTransaction, MLMSettings
from mlm.services import generate_mlm_commissions_for_order

class Command(BaseCommand):
    help = "Crée une upline N1->N2->N3 + 3 commandes 'done' (10k, 20k, 30k) et génère les commissions."

    @transaction.atomic
    def handle(self, *args, **options):
        settings_obj = MLMSettings.get_active()
        self.stdout.write(f"MLM actif : {settings_obj} (N1={settings_obj.level1_percent}%, N2={settings_obj.level2_percent}%, N3={settings_obj.level3_percent}%)")

        # Nettoyage des anciens éléments démo
        ReferralCommission.objects.all().delete()
        WalletTransaction.objects.filter(type="mlm_commission").delete()
        Customer.objects.filter(name__icontains="DEMO MLM").delete()
        ReferralLink.objects.filter(referral_code__in=["DEMO_N1","DEMO_N2","DEMO_N3"]).delete()

        # Chaîne démo
        n1 = ReferralLink.objects.create(referral_code="DEMO_N1")
        n2 = ReferralLink.objects.create(referral_code="DEMO_N2", sponsor=n1)
        client = Customer.objects.create(name="Client DEMO MLM", phone="0700000012", address="Cocody")
        n3 = ReferralLink.objects.create(referral_code="DEMO_N3", sponsor=n2, customer=client)

        amounts = [Decimal("10000.00"), Decimal("20000.00"), Decimal("30000.00")]
        for amt in amounts:
            o = Order.objects.create(customer=client, status="pending")
            OrderItem.objects.create(order=o, designation=f"Panier {amt}", quantity=1, unit_price=amt)
            o.status = "done"; o.save(); o.refresh_from_db()
            generate_mlm_commissions_for_order(o)

        # Récap
        total_comm = ReferralCommission.objects.count()
        from django.db.models import Sum
        from mlm.models import ReferralCommission as RC
        n1_total = RC.objects.filter(beneficiary_profile=n1).aggregate(s=Sum("commission_amount"))["s"] or 0
        n2_total = RC.objects.filter(beneficiary_profile=n2).aggregate(s=Sum("commission_amount"))["s"] or 0
        n3_total = RC.objects.filter(beneficiary_profile=n3).aggregate(s=Sum("commission_amount"))["s"] or 0
        self.stdout.write(self.style.SUCCESS(f"Seed OK. Commissions={total_comm} | N1={n1_total} FCFA | N2={n2_total} FCFA | N3={n3_total} FCFA"))
