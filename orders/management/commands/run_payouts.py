from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from datetime import timedelta

def run_payouts_batch(dry_run=True, hold_hours=24):
    """
    Batch payout simple:
    - prend toutes les transactions "earning" older than hold
    - crée une transaction "payout" par driver pour le total dispo
    - idempotent: si payout déjà fait pour la fenêtre, on ne double pas (simple check par note)
    """
    from wallets.models import Wallet, WalletTransaction  # existant chez toi
    from orders.models import DeliveryPartner

    now = timezone.now()
    hold_before = now - timedelta(hours=int(hold_hours))

    result = {"drivers": 0, "total_paid": 0.0, "dry_run": dry_run, "hold_hours": hold_hours}

    # Hypothèse: wallet owner_type="driver" + owner_id=DeliveryPartner.id (comme tu fais déjà)
    drivers = DeliveryPartner.objects.all().order_by("id")

    for d in drivers:
        w = Wallet.objects.filter(owner_type="driver", owner_id=d.id).first()
        if not w:
            continue

        # earnings dispo = earning <= hold_before (tu ajustes selon ton modèle)
        qs = WalletTransaction.objects.filter(wallet=w, type="earning", direction="in", created_at__lte=hold_before)
        total = float(sum([float(x.amount) for x in qs]))

        if total <= 0:
            continue

        # idempotence simple: on évite de payer 2 fois sur le même total si déjà payout "batch" aujourd'hui
        today = now.date().isoformat()
        already = WalletTransaction.objects.filter(wallet=w, type="payout", meta__icontains=f"batch:{today}").exists()
        if already:
            continue

        if dry_run:
            result["drivers"] += 1
            result["total_paid"] += total
            continue

        with transaction.atomic():
            WalletTransaction.objects.create(
                wallet=w,
                type="payout",
                direction="in",
                amount=total,
                meta=f"batch:{today}",
            )
        result["drivers"] += 1
        result["total_paid"] += total

    return result


class Command(BaseCommand):
    help = "Run daily payouts to driver wallets (batch)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", default=False)
        parser.add_argument("--hold-hours", type=int, default=24)

    def handle(self, *args, **opts):
        dry = bool(opts["dry_run"])
        hold = int(opts["hold_hours"])
        res = run_payouts_batch(dry_run=dry, hold_hours=hold)
        self.stdout.write(self.style.SUCCESS(f"OK payout batch: {res}"))
