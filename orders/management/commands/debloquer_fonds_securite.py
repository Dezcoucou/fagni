"""
FAGNI — Deblocage hebdomadaire Fonds Securite & Solidarite (VERSION SECURISEE V2)
Usage  : python manage.py debloquer_fonds_securite [--dry-run] [--livreur-id ID]
Cron   : Chaque lundi a 08h00 (PythonAnywhere Tasks)
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import transaction
from decimal import Decimal
import logging

logger = logging.getLogger("fagni.fonds_securite")


class Command(BaseCommand):
    help = "Deblocage hebdomadaire Fonds Securite & Solidarite — V2 SECURISEE"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
            help='Simulation sans modification')
        parser.add_argument('--livreur-id', type=int,
            help='Traiter uniquement ce livreur (debug)')

    def handle(self, *args, **options):
        dry_run    = options['dry_run']
        livreur_id = options.get('livreur_id')
        now        = timezone.now()
        iso        = now.isocalendar()
        week_key   = f"{iso[0]}-W{iso[1]:02d}"
        lundi      = now - timezone.timedelta(days=7)
        mode       = "[DRY-RUN]" if dry_run else "[PRODUCTION]"

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"FAGNI Fonds Securite {mode} — Semaine {week_key}")
        self.stdout.write(f"{'='*60}\n")

        from wallets.models import Wallet, WalletTransaction
        from orders.models import FagniEvent

        qs = Wallet.objects.filter(
            delivery_partner__isnull=False,
            balance_securite__gt=0,
        ).select_related('delivery_partner')

        if livreur_id:
            qs = qs.filter(delivery_partner__id=livreur_id)

        total_debloque = 0
        nb_debloque = nb_skip = nb_error = 0

        for wallet in qs:
            driver = wallet.delivery_partner
            ikey   = f"fonds_securite:unlock:{driver.id}:{week_key}"

            # Anti double deblocage
            if WalletTransaction.objects.filter(idempotency_key=ikey).exists():
                self.stdout.write(f"SKIP {driver.name} — deja traite {week_key}")
                nb_skip += 1
                continue

            # Verification incidents
            incidents = FagniEvent.objects.filter(
                actor_type='driver',
                actor_id=str(driver.id),
                event_type__in=['mission.abandoned','incident','litige','fraud.detected'],
                created_at__gte=lundi,
            ).count()

            if incidents > 0:
                self.stdout.write(f"BLOQUE {driver.name} — {incidents} incident(s)")
                nb_skip += 1
                if not dry_run:
                    try:
                        FagniEvent.objects.create(
                            event_type='fonds_securite.blocked', actor_type='system',
                            actor_id='cron', order=None,
                            data={'driver_id': driver.id, 'week': week_key, 'incidents': incidents},
                        )
                    except Exception: pass
                continue

            montant = wallet.balance_securite.quantize(Decimal("0.01"))
            if montant <= 0:
                continue

            if dry_run:
                self.stdout.write(f"[DRY-RUN] {driver.name} — {montant} FCFA seraient debloquees")
                total_debloque += int(montant)
                nb_debloque += 1
                continue

            try:
                with transaction.atomic():
                    w = Wallet.objects.select_for_update().get(pk=wallet.pk)
                    if w.balance_securite <= 0:
                        nb_skip += 1
                        continue
                    montant = w.balance_securite.quantize(Decimal("0.01"))
                    w.balance          += montant
                    w.balance_securite  = Decimal("0.00")
                    w.save(update_fields=["balance", "balance_securite", "updated_at"])

                    WalletTransaction.objects.create(
                        wallet=w, order=None, leg=None,
                        type='credit',
                        direction='in',
                        amount=montant,
                        description=(
                            f"[UNLOCK_SECURITE] Deblocage Fonds Securite semaine {week_key} | "
                            f"livreur={driver.name} | id={driver.id} | montant={montant} FCFA"
                        ),
                        idempotency_key=ikey,
                        allow_orphan=True,
                    )

                    try:
                        FagniEvent.objects.create(
                            event_type='fonds_securite.unlocked', actor_type='system',
                            actor_id='cron', order=None,
                            data={'driver_id': driver.id, 'driver_name': driver.name,
                                  'week': week_key, 'montant': str(montant)},
                        )
                    except Exception: pass

                self.stdout.write(f"OK {driver.name} — {montant} FCFA debloquees")
                total_debloque += int(montant)
                nb_debloque += 1

            except Exception as e:
                nb_error += 1
                logger.error(f"Erreur driver={driver.id}: {e}", exc_info=True)
                self.stdout.write(self.style.ERROR(f"ERREUR {driver.name}: {e}"))

        self.stdout.write(f"\n{'='*60}")
        self.stdout.write(f"Debloquees : {nb_debloque} livreurs | {total_debloque:,} FCFA")
        self.stdout.write(f"Skips      : {nb_skip} | Erreurs : {nb_error}")
        self.stdout.write(f"{'='*60}\n")
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN — Aucune modification effectuee."))
