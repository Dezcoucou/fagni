from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from orders.models import Customer, Order, OrderItem
from mlm.models import ReferralLink, ReferralCommission, WalletTransaction, MLMSettings
from mlm.services import generate_mlm_commissions_for_order


class Command(BaseCommand):
    help = "Génère un jeu de données de démonstration pour le module MLM FAGNI."

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== SEED MLM DEMO FAGNI ==="))

        # 1) S'assurer qu'on a un paramétrage MLM actif
        settings_obj = MLMSettings.get_active()
        self.stdout.write(
            self.style.SUCCESS(
                f"Configuration MLM active : {settings_obj} "
                f"(N1={settings_obj.level1_percent}%, "
                f"N2={settings_obj.level2_percent}%, "
                f"N3={settings_obj.level3_percent}%)"
            )
        )

        # 2) Nettoyer éventuellement un ancien jeu de démo
        #    (on filtre sur des codes bien spécifiques pour ne pas toucher à tes vrais affiliés)
        self.stdout.write("Suppression des anciennes données de démo (si présentes)...")

        demo_codes = ["N1_FULL_DEMO", "N2_FULL_DEMO", "N3_FULL_DEMO"]
        ReferralCommission.objects.filter(
            order__customer__name__icontains="Client N3 Full Demo"
        ).delete()
        WalletTransaction.objects.filter(
            profile__referral_code__in=demo_codes
        ).delete()
        ReferralLink.objects.filter(referral_code__in=demo_codes).delete()
        Customer.objects.filter(name__icontains="Client N3 Full Demo").delete()

        # 3) Création d'un client N3 et de la chaîne de parrainage N1 -> N2 -> N3
        self.stdout.write("Création de la chaîne N1 -> N2 -> N3...")

        client = Customer.objects.create(
            name="Client N3 Full Demo",
            phone="0700000011",
            address="Cocody",
        )

        n1 = ReferralLink.objects.create(
            referral_code="N1_FULL_DEMO",
            sponsor=None,
            customer=None,
        )
        n2 = ReferralLink.objects.create(
            referral_code="N2_FULL_DEMO",
            sponsor=n1,
            customer=None,
        )
        n3 = ReferralLink.objects.create(
            referral_code="N3_FULL_DEMO",
            sponsor=n2,
            customer=client,
        )

        self.stdout.write(self.style.SUCCESS("Chaîne MLM créée : N1_FULL_DEMO -> N2_FULL_DEMO -> N3_FULL_DEMO."))

        # 4) Création de quelques commandes pour ce client
        self.stdout.write("Création de commandes terminées pour le client N3...")

        amounts = [Decimal("10000.00"), Decimal("20000.00"), Decimal("30000.00")]
        created_orders = []

        for idx, amount in enumerate(amounts, start=1):
            order = Order.objects.create(
                customer=client,
                status="pending",
            )

            OrderItem.objects.create(
                order=order,
                designation=f"Panier test MLM {idx}",
                quantity=1,
                unit_price=amount,
            )

            # On passe la commande en 'done' pour déclencher la logique
            order.status = "done"
            order.save()  # recalcul total + service_fee

            # Par sécurité, on force la génération (la fonction gère le cas "déjà généré")
            generate_mlm_commissions_for_order(order)

            created_orders.append(order)

        self.stdout.write(self.style.SUCCESS(f"{len(created_orders)} commandes créées et marquées comme 'done'."))

        # 5) Récap commissions & wallet
        total_commissions = ReferralCommission.objects.filter(order__in=created_orders).count()
        total_wallet_txs = WalletTransaction.objects.filter(order__in=created_orders, type="mlm_commission").count()

        self.stdout.write(self.style.MIGRATE_HEADING("=== RÉCAP DEMO MLM ==="))
        self.stdout.write(f"Client affilié : {client.name} ({client.phone})")
        self.stdout.write(f"Profil N3       : {n3.referral_code}")
        self.stdout.write(f"Profil N2       : {n2.referral_code}")
        self.stdout.write(f"Profil N1       : {n1.referral_code}")
        self.stdout.write("")
        self.stdout.write(f"Nombre de commandes de démo : {len(created_orders)}")
        self.stdout.write(f"Nombre de commissions MLM   : {total_commissions}")
        self.stdout.write(f"Transactions wallet (mlm)   : {total_wallet_txs}")

        # Détail par niveau
        for level in [1, 2, 3]:
            qs = ReferralCommission.objects.filter(order__in=created_orders, level=level)
            s = sum((c.commission_amount for c in qs), 0)
            self.stdout.write(f" - Total niveau N{level} : {s} FCFA")

        self.stdout.write(self.style.SUCCESS("Seed MLM de démonstration terminé."))
