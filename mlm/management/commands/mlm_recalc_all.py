from django.core.management.base import BaseCommand
from django.db import transaction

from orders.models import Order
from mlm.models import ReferralCommission, WalletTransaction
from mlm.services import generate_mlm_commissions_for_order


class Command(BaseCommand):
    help = "Recalcule proprement toutes les commissions MLM sur les commandes 'done' (nettoie avant chaque recalcul)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Affiche ce qui serait fait sans écrire en base.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        dry = options["dry_run"]
        qs = Order.objects.filter(status="done").order_by("id")
        self.stdout.write(
            self.style.WARNING(f"Commandes 'done' trouvées : {qs.count()}")
        )

        total_deleted_comm = 0
        total_deleted_wallet = 0
        total_rebuilt = 0

        for order in qs:
            # Nettoyage pour éviter les doublons sur cette commande
            del_comm, _ = ReferralCommission.objects.filter(order=order).delete()
            del_wal, _ = WalletTransaction.objects.filter(
                order=order,
                type="mlm_commission",
            ).delete()

            total_deleted_comm += del_comm
            total_deleted_wallet += del_wal

            if not dry:
                generate_mlm_commissions_for_order(order)
                total_rebuilt += 1

        # Récap global
        self.stdout.write(
            self.style.SUCCESS(f"Commissions supprimées : {total_deleted_comm}")
        )
        self.stdout.write(
            self.style.SUCCESS(f"Wallet tx supprimées : {total_deleted_wallet}")
        )

        if dry:
            self.stdout.write(self.style.NOTICE("[DRY RUN] Aucun recalcul écrit en base."))
        else:
            self.stdout.write(
                self.style.SUCCESS(f"Commandes recalculées : {total_rebuilt}")
            )
