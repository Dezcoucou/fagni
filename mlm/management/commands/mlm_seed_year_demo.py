from decimal import Decimal
import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction

from mlm.models import (
    MLMSettings,
    ReferralLink,
    ReferralCommission,
    WalletTransaction,
)
from orders.models import Customer, Order, OrderItem
from mlm.services import generate_mlm_commissions_for_order


class Command(BaseCommand):
    help = (
        "Seed MLM réaliste sur 12 mois : "
        "1 chaîne N1→N2→N3, commandes réparties sur l'année, "
        "commissions générées et récapitulatif."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        self.stdout.write(self.style.MIGRATE_HEADING("=== SEED MLM YEAR DEMO (12 mois) ==="))

        # -------------------------------------------------
        # 1) Nettoyage complet des anciennes données YEAR DEMO
        # -------------------------------------------------
        self.stdout.write("Nettoyage des anciennes données YEAR DEMO...")

        deleted_comm, _ = ReferralCommission.objects.filter(
            beneficiary_profile__referral_code__contains="_YEARDEMO_"
        ).delete()

        deleted_wallet, _ = WalletTransaction.objects.filter(
            profile__referral_code__contains="_YEARDEMO_",
            type="mlm_commission",
        ).delete()

        deleted_links, _ = ReferralLink.objects.filter(
            referral_code__contains="_YEARDEMO_"
        ).delete()

        deleted_customers, _ = Customer.objects.filter(
            name__icontains="YEAR DEMO MLM"
        ).delete()

        self.stdout.write(
            f"Commissions supprimées : {deleted_comm}, "
            f"Wallet tx supprimées : {deleted_wallet}, "
            f"Profils MLM supprimés : {deleted_links}, "
            f"Clients supprimés : {deleted_customers}"
        )

        # -------------------------------------------------
        # 2) Récupération de la configuration MLM active
        # -------------------------------------------------
        settings_obj = MLMSettings.get_active()
        self.stdout.write(
            self.style.NOTICE(
                f"Configuration MLM active : {settings_obj} "
                f"(N1={settings_obj.level1_percent}%, "
                f"N2={settings_obj.level2_percent}%, "
                f"N3={settings_obj.level3_percent}%)"
            )
        )

        # -------------------------------------------------
        # 3) Création de la chaîne d'affiliation N1 → N2 → N3
        # -------------------------------------------------
        n1, _ = ReferralLink.objects.get_or_create(
            referral_code="AFF_N1_YEARDEMO",
            defaults={
                "sponsor": None,
                "customer": None,
                "actor_type": "influencer",
            },
        )

        n2, _ = ReferralLink.objects.get_or_create(
            referral_code="AFF_N2_YEARDEMO",
            defaults={
                "sponsor": n1,
                "customer": None,
                "actor_type": "influencer",
            },
        )

        client, _ = Customer.objects.get_or_create(
            name="CLIENT YEAR DEMO MLM",
            phone="0700008888",
            defaults={
                "address": "Cocody – Démo MLM",
            },
        )

        n3, _ = ReferralLink.objects.get_or_create(
            referral_code="AFF_N3_YEARDEMO",
            defaults={
                "sponsor": n2,
                "customer": client,
                "actor_type": "client",
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Chaîne créée : {n1.referral_code} → {n2.referral_code} → {n3.referral_code}"
            )
        )

        # -------------------------------------------------
        # 4) Génération des commandes sur 12 mois
        # -------------------------------------------------
        today = timezone.now().date()
        montant_possibles = [
            Decimal("5000.00"),
            Decimal("8000.00"),
            Decimal("10000.00"),
            Decimal("15000.00"),
            Decimal("20000.00"),
        ]

        total_orders_created = 0

        self.stdout.write("Création de commandes réparties sur 12 mois...")

        # On part de "aujourd'hui" et on remonte mois par mois
        for month_index in range(12):
            # Base "approximative" du mois : today - 30 * month_index jours
            month_base = today - timedelta(days=30 * month_index)

            # Nombre de commandes ce mois (entre 2 et 8 pour faire réaliste)
            nb_orders_this_month = random.randint(2, 8)

            for _ in range(nb_orders_this_month):
                # Décale la date de quelques jours dans le mois (0 à 29)
                order_date = month_base - timedelta(days=random.randint(0, 29))

                # 1) Création commande en pending
                order = Order.objects.create(
                    customer=client,
                    status="pending",
                )

                # 2) Création d'un panier simple avec un montant HT aléatoire
                ht_amount = random.choice(montant_possibles)
                OrderItem.objects.create(
                    order=order,
                    designation="Panier Year Demo MLM",
                    quantity=1,
                    unit_price=ht_amount,
                )

                # 3) Marquer la commande comme "done" pour déclencher la logique métier
                order.status = "done"
                order.save()  # recalcule total_ht, service_fee, etc.
                # On force created_at / updated_at pour coller à la date simulée
                Order.objects.filter(pk=order.pk).update(
                    created_at=order_date,
                    updated_at=order_date,
                )
                order.refresh_from_db()

                # 4) Générer explicitement les commissions MLM (en plus du signal)
                generate_mlm_commissions_for_order(order)

                total_orders_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"{total_orders_created} commandes 'done' créées sur 12 mois."
            )
        )

        # -------------------------------------------------
        # 5) Récapitulatif global MLM YEAR DEMO
        # -------------------------------------------------
        total_comm = ReferralCommission.objects.filter(
            beneficiary_profile__referral_code__contains="_YEARDEMO"
        ).count()

        total_wallet = WalletTransaction.objects.filter(
            profile__referral_code__contains="_YEARDEMO",
            type="mlm_commission",
        ).count()

        self.stdout.write(self.style.MIGRATE_HEADING("=== RÉCAP YEAR DEMO MLM ==="))
        self.stdout.write(f"Client démonstration  : {client} ({client.phone})")
        self.stdout.write(f"N1 : {n1.referral_code}")
        self.stdout.write(f"N2 : {n2.referral_code}")
        self.stdout.write(f"N3 : {n3.referral_code}")
        self.stdout.write(f"Commandes créées      : {total_orders_created}")
        self.stdout.write(f"Commissions générées  : {total_comm}")
        self.stdout.write(f"Transactions wallet   : {total_wallet}")

        # Détail par niveau
        for lvl in (1, 2, 3):
            total_lvl = sum(
                (
                    c.commission_amount
                    for c in ReferralCommission.objects.filter(
                        beneficiary_profile__referral_code__contains="_YEARDEMO",
                        level=lvl,
                    )
                ),
                0,
            )
            self.stdout.write(f"Total N{lvl} (YEAR DEMO) : {total_lvl} FCFA")

        # Détail par profil
        for profile in (n1, n2, n3):
            total_profile = sum(
                (
                    tx.amount
                    for tx in WalletTransaction.objects.filter(
                        profile=profile,
                        type="mlm_commission",
                    )
                ),
                0,
            )
            self.stdout.write(
                f"Wallet {profile.referral_code} : {total_profile} FCFA"
            )

        self.stdout.write(self.style.SUCCESS("Seed MLM YEAR DEMO terminé avec succès !"))
