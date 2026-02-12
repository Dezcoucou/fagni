from django.core.management.base import BaseCommand
from django.db.models import Count, Q
from django.db import transaction

class Command(BaseCommand):
    help = "Audit & auto-fix: cohérence DeliveryLeg <-> WalletTransaction (payouts) + statuts/driver_amount."

    def add_arguments(self, parser):
        parser.add_argument("--fix", action="store_true", help="Applique les corrections (sinon audit seulement).")

    def handle(self, *args, **options):
        fix = options["fix"]

        from orders.models import DeliveryLeg
        from wallets.models import WalletTransaction

        # 1) Legs payés mais pas done
        paid_leg_ids = set(
            WalletTransaction.objects.filter(
                wallet__owner_type="driver",
                type="payout",
                direction="in",
                leg__isnull=False,
            ).values_list("leg_id", flat=True)
        )

        legs_paid_not_done = DeliveryLeg.objects.filter(id__in=paid_leg_ids).exclude(status="done")
        self.stdout.write(self.style.WARNING(f"legs_paid_not_done={legs_paid_not_done.count()}"))

        # 2) Legs payés mais driver_amount != tx.amount
        # (on prend la dernière tx payout/in par leg)
        mismatch = []
        tx_by_leg = (
            WalletTransaction.objects.filter(
                wallet__owner_type="driver",
                type="payout",
                direction="in",
                leg__isnull=False,
            )
            .order_by("leg_id", "-id")
        )

        last_tx = {}
        for tx in tx_by_leg.iterator():
            if tx.leg_id not in last_tx:
                last_tx[tx.leg_id] = tx

        for leg_id, tx in last_tx.items():
            try:
                leg = DeliveryLeg.objects.get(id=leg_id)
            except DeliveryLeg.DoesNotExist:
                continue
            if str(getattr(leg, "driver_amount", None)) != str(tx.amount):
                mismatch.append((leg.id, leg.order_id, leg.leg_type, str(leg.driver_amount), str(tx.amount)))

        self.stdout.write(self.style.WARNING(f"driver_amount_mismatch={len(mismatch)}"))
        if mismatch[:20]:
            self.stdout.write("sample_mismatch=" + str(mismatch[:20]))

        # 3) Duplicates (ne devrait plus exister depuis ton fix)
        dups = (
            DeliveryLeg.objects.values("order_id", "leg_type")
            .annotate(n=Count("id"))
            .filter(n__gt=1)
        )
        self.stdout.write(self.style.WARNING(f"duplicate_leg_groups={dups.count()}"))
        if dups.count() and list(dups[:20]):
            self.stdout.write("sample_dups=" + str(list(dups[:20])))

        # 4) Legs en statut actif sans driver (doit être 0 grâce à ton guard)
        active_no_driver = DeliveryLeg.objects.filter(
            driver__isnull=True,
            status__in=("assigned", "in_progress", "done"),
        )
        self.stdout.write(self.style.WARNING(f"active_legs_without_driver={active_no_driver.count()}"))

        if not fix:
            self.stdout.write(self.style.SUCCESS("AUDIT ONLY (use --fix to apply changes)."))
            return

        # =========================
        # APPLY FIXES
        # =========================
        with transaction.atomic():
            # A) Forcer legs payés -> done
            updated_done = legs_paid_not_done.update(status="done")

            # B) Recaler driver_amount sur tx.amount (source-of-truth)
            fixed_amount = 0
            for leg_id, tx in last_tx.items():
                try:
                    leg = DeliveryLeg.objects.get(id=leg_id)
                except DeliveryLeg.DoesNotExist:
                    continue
                # On ne touche pas si déjà ok
                if str(getattr(leg, "driver_amount", None)) == str(tx.amount):
                    continue
                leg.driver_amount = tx.amount
                leg.save(update_fields=["driver_amount"])
                fixed_amount += 1

            # C) Forcer active sans driver -> pending (sécurité)
            fixed_active = 0
            for leg in active_no_driver.iterator():
                leg.status = "pending"
                leg.save(update_fields=["status"])
                fixed_active += 1

        self.stdout.write(self.style.SUCCESS(
            f"FIXED: legs_set_done={updated_done}, driver_amount_fixed={fixed_amount}, active_no_driver_fixed={fixed_active}"
        ))
