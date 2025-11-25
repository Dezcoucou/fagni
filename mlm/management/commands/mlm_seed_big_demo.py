from django.core.management.base import BaseCommand
from django.db import transaction
from decimal import Decimal
import random

from orders.models import Customer, Order, OrderItem
from mlm.models import ReferralLink, ReferralCommission, WalletTransaction, MLMSettings
from mlm.services import generate_mlm_commissions_for_order


class Command(BaseCommand):
    help = "Crée un gros scénario de démonstration affilié : N1 → N2 → N3, 50 commandes terminées, commissions générées."

    @transaction.atomic
    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.WARNING("=== SEED MLM BIG DEMO (50 commandes) ==="))

        # 1) Nettoyage complet de toutes les anciennes données demo
        ReferralCommission.objects.filter(
            beneficiary_profile__referral_code__contains="_BIGDEMO_"
        ).delete()

        WalletTransaction.objects.filter(
            profile__referral_code__contains="_BIGDEMO_"
        ).delete()

        ReferralLink.objects.filter(
            referral_code__contains="_BIGDEMO_"
        ).delete()

        Customer.objects.filter(
            name__icontains="BIGDEMO"
        ).delete()   # plus sécurisé

        Order.objects.filter(
            customer__name__icontains="BIGDEMO"
        ).delete()


        # 2) Configuration MLM
        settings_obj = MLMSettings.get_active()
        self.stdout.write(
            f"Configuration MLM : N1={settings_obj.level1_percent}%, N2={settings_obj.level2_percent}%"
        )

        # 3) Création du client démo
        client = Customer.objects.create(
            name="CLIENT BIG DEMO AFFILIÉ",
            phone="0700008888",
            address="Abidjan - Cocody",
        )

        # 4) Chaîne affiliée
        n1 = ReferralLink.objects.create(referral_code="AFF_N1_BIGDEMO", sponsor=None)
        n2 = ReferralLink.objects.create(referral_code="AFF_N2_BIGDEMO", sponsor=n1)
        n3 = ReferralLink.objects.create(
            referral_code="AFF_N3_BIGDEMO",
            sponsor=n2,
            customer=client,
        )

        self.stdout.write(
            self.style.SUCCESS(f"Chaîne créée : {n1.referral_code} → {n2.referral_code} → {n3.referral_code}")
        )

        # 5) Génération des 50 commandes
        amounts = [
            random.choice([5000, 7000, 10000, 12000, 15000, 20000, 25000])
            for _ in range(50)
        ]

        orders_created = []
        for amount in amounts:
            order = Order.objects.create(customer=client, status="pending")
            OrderItem.objects.create(
                order=order,
                designation=f"Commande BIG DEMO {amount} FCFA",
                quantity=1,
                unit_price=Decimal(str(amount)),
            )

            order.status = "done"
            order.save()
            order.refresh_from_db()

            generate_mlm_commissions_for_order(order)
            orders_created.append(order)


        # 6) Récapitulatif
        total_comm = ReferralCommission.objects.filter(
            beneficiary_profile__referral_code__contains="_BIGDEMO_"
        ).count()

        total_wallet = WalletTransaction.objects.filter(
            profile__referral_code__contains="_BIGDEMO_",
            type="mlm_commission"
        ).count()

        self.stdout.write(self.style.WARNING("\n=== RÉCAP BIG DEMO ==="))
        self.stdout.write(f"Client BIG DEMO     : {client.name} ({client.phone})")
        self.stdout.write(f"Commandes créées    : {len(orders_created)}")
        self.stdout.write(f"Commissions générées: {total_comm}")
        self.stdout.write(f"Wallet transactions : {total_wallet}")

        # Totaux par niveau
        for lvl, code in [(1, n1.referral_code), (2, n2.referral_code)]:
            total_lvl = sum(
                tx.amount
                for tx in WalletTransaction.objects.filter(
                    profile__referral_code=code,
                    type="mlm_commission"
                )
            )
            self.stdout.write(
                self.style.SUCCESS(f"Total commissions N{lvl} ({code}) : {total_lvl} FCFA")
            )

        self.stdout.write(self.style.SUCCESS("\nBIG DEMO MLM généré avec succès !"))
