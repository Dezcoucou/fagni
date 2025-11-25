from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal

from orders.models import Customer, Order, OrderItem
from mlm.models import ReferralLink, ReferralCommission, WalletTransaction, MLMSettings
from mlm.services import generate_mlm_commissions_for_order


class Command(BaseCommand):
    help = "Crée un scénario affilié de démonstration (N1 → N2 → N3 + 3 commandes)."

    @transaction.atomic
    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("=== SEED MLM DEMO AFFILIATE ==="))

        # Nettoyage des anciens seeds
        ReferralCommission.objects.filter(beneficiary_profile__referral_code__contains="_DEMO_").delete()
        WalletTransaction.objects.filter(profile__referral_code__contains="_DEMO_").delete()
        ReferralLink.objects.filter(referral_code__contains="_DEMO_").delete()
        Customer.objects.filter(name__contains="DEMO AFFILIÉ").delete()

        self.stdout.write("Nettoyage des anciennes données de démo effectué.")

        # Configuration MLM obligatoire
        settings_obj = MLMSettings.get_active()
        self.stdout.write(f"Configuration MLM active : {settings_obj} (N1={settings_obj.level1_percent}%, N2={settings_obj.level2_percent}%)")

        # 1) Création client
        client = Customer.objects.create(
            name="CLIENT DEMO AFFILIÉ",
            phone="0700007777",
            address="Cocody",
        )

        # 2) Chaîne affiliée : N1 -> N2 -> N3(client)
        n1 = ReferralLink.objects.create(referral_code="AFF_N1_DEMO_AFF", sponsor=None)
        n2 = ReferralLink.objects.create(referral_code="AFF_N2_DEMO_AFF", sponsor=n1)
        n3 = ReferralLink.objects.create(referral_code="AFF_N3_DEMO_AFF", sponsor=n2, customer=client)

        self.stdout.write(self.style.SUCCESS(f"Chaîne affiliée créée : {n1.referral_code} → {n2.referral_code} → {n3.referral_code}"))

        # 3) Génération de 3 commandes terminées
        amounts = [8000, 12000, 20000]  # FCFA HT

        created_orders = []
        for amount in amounts:
            order = Order.objects.create(customer=client, status="pending")
            OrderItem.objects.create(
                order=order,
                designation=f"Commande démo {amount} FCFA",
                quantity=1,
                unit_price=Decimal(str(amount)),
            )

            order.status = "done"
            order.save()
            order.refresh_from_db()

            generate_mlm_commissions_for_order(order)
            created_orders.append(order)

        self.stdout.write(self.style.SUCCESS(f"{len(created_orders)} commandes créées et marquées comme 'done'."))

        # 4) Récapitulatif
        total_comm = ReferralCommission.objects.filter(beneficiary_profile__referral_code__contains="_DEMO_AFF").count()
        total_wallet = WalletTransaction.objects.filter(profile__referral_code__contains="_DEMO_AFF").count()

        self.stdout.write(self.style.WARNING("\n=== RÉCAP DEMO AFFILIATE ==="))
        self.stdout.write(f"Client démonstration  : {client.name} ({client.phone})")
        self.stdout.write(f"N1 : {n1.referral_code}")
        self.stdout.write(f"N2 : {n2.referral_code}")
        self.stdout.write(f"N3 : {n3.referral_code}")
        self.stdout.write(f"Commandes créées      : {len(created_orders)}")
        self.stdout.write(f"Commissions générées  : {total_comm}")
        self.stdout.write(f"Transactions wallet   : {total_wallet}")

        self.stdout.write(self.style.SUCCESS("Seed MLM affilié terminé avec succès !"))
