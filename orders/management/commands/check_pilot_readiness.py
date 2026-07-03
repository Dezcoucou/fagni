from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.db import models


class Command(BaseCommand):
    help = (
        "Check PILOT readiness (guards) for orders: paid/unpaid consistency, required partners, legs, "
        "wallet distribution flags, and wallet tx presence. "
        "Options: --only=paid|unpaid|all, --limit, --fix, --dry-run, --verbose"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--only",
            choices=["paid", "unpaid", "all"],
            default="all",
            help="Filtrer sur les commandes paid/unpaid/all",
        )
        parser.add_argument("--limit", type=int, default=200, help="Limiter le nombre d'anomalies affichées")
        parser.add_argument("--fix", action="store_true", help="Applique des corrections SAFE (sync paiement)")
        parser.add_argument("--dry-run", action="store_true", help="Forcer dry-run (ne modifie rien)")
        parser.add_argument("--verbose", action="store_true", help="Détails par commande")

    def _dec(self, x) -> Decimal:
        try:
            return Decimal(str(x or 0))
        except Exception:
            return Decimal("0")

    def _get_attr_first(self, obj, names, default=None):
        for n in names:
            if hasattr(obj, n):
                v = getattr(obj, n)
                return v() if callable(v) else v
        return default

    def handle(self, *args, **opts):
        from django.apps import apps

        Order = apps.get_model("orders", "Order")
        DeliveryLeg = apps.get_model("orders", "DeliveryLeg")
        WalletTransaction = apps.get_model("wallets", "WalletTransaction")

        # Payment model might not exist in your codebase — we handle gracefully.
        try:
            Payment = apps.get_model("orders", "Payment")
        except Exception:
            Payment = None

        only = opts["only"]
        limit = int(opts["limit"])
        verbose = bool(opts["verbose"])

        do_fix = bool(opts["fix"])
        dry_run = bool(opts["dry_run"]) or (not do_fix)

        self.stdout.write(self.style.WARNING(f"CHECK PILOT READINESS — dry_run={dry_run}, fix={do_fix}"))
        self.stdout.write("")

        qs = Order.objects.all().order_by("id")

        if only in ("paid", "unpaid"):
            qs = qs.filter(payment_status=("paid" if only == "paid" else "unpaid"))

        total = qs.count()

        # Buckets
        paid_without_laundry = []
        paid_without_delivery_partner = []
        paid_without_legs = []
        paid_but_wallets_not_distributed = []
        inconsistent_payment_status = []
        unpaid_with_wallet_tx = []

        fixed_count = 0

        def compute_amount_paid(order):
            # Preferred: Payment table sum (if exists)
            if Payment is not None:
                try:
                    q = Payment.objects.filter(order=order)
                    # If there is a status field, keep only successful/paid
                    if hasattr(Payment, "status"):
                        q = q.filter(status__in=["paid", "success", "successful", "succeeded"])
                    amt = q.aggregate(s=Sum("amount"))["s"]
                    if amt is not None:
                        return self._dec(amt)
                except Exception:
                    import logging
                    logging.getLogger("fagni.orders.management.commands.check_pilot_readiness").exception("Exception silencieuse (auto-log) - fichier=orders/management/commands/check_pilot_readiness.py ligne=90")

            # Fallback: order fields (if exist)
            return self._dec(self._get_attr_first(order, ["amount_paid", "paid_amount", "total_paid"], default=0))

        def compute_total_ttc(order):
            # Try common field names used in FAGNI templates / models
            return self._dec(
                self._get_attr_first(
                    order,
                    [
                        "total_client_ttc",
                        "total_ttc",
                        "total_amount_ttc",
                        "total_amount",
                        "total",
                    ],
                    default=0,
                )
            )

        def get_partner(order, names):
            # Accept FK or property
            return self._get_attr_first(order, names, default=None)

        def show_row(i, order, tag, extra=""):
            code = self._get_attr_first(order, ["code"], default="")
            pay = self._get_attr_first(order, ["payment_status"], default=None)
            status = self._get_attr_first(order, ["status"], default=None)
            self.stdout.write(
                f"{i:03d} | order={order.id} {code} pay={pay} status={status} | {tag}{(' | ' + extra) if extra else ''}"
            )

        # Main scan
        for order in qs.iterator():
            pay_status = self._get_attr_first(order, ["payment_status"], default=None)

            total_ttc = compute_total_ttc(order)
            amount_paid = compute_amount_paid(order)

            wallets_distributed = bool(self._get_attr_first(order, ["wallets_distributed"], default=False))

            laundry = get_partner(order, ["laundry_partner", "laundry", "laundry_partner_obj"])
            delivery_partner = get_partner(order, ["delivery_partner", "delivery_company", "delivery_partner_obj"])

            legs_count = DeliveryLeg.objects.filter(order=order).count()

            # A) Payment consistency guards (pilot)
            # États autorisés : unpaid / partial / paid
            if pay_status == "paid":
                # paid => amount_paid doit couvrir total (si total > 0)
                if total_ttc > 0 and amount_paid < total_ttc:
                    inconsistent_payment_status.append((order, total_ttc, amount_paid))
            elif pay_status == "partial":
                # partial => doit être > 0 et < total (si total > 0)
                if amount_paid <= 0:
                    inconsistent_payment_status.append((order, total_ttc, amount_paid))
                elif total_ttc > 0 and amount_paid >= total_ttc:
                    inconsistent_payment_status.append((order, total_ttc, amount_paid))
            else:
                # unpaid (ou autre) => doit être 0
                if amount_paid > 0:
                    inconsistent_payment_status.append((order, total_ttc, amount_paid))

            # B) Pilot required partners / legs (only for paid orders)
            if pay_status == "paid":
                if not laundry:
                    paid_without_laundry.append(order)
                if not delivery_partner:
                    paid_without_delivery_partner.append(order)
                if legs_count <= 0:
                    paid_without_legs.append(order)
                if not wallets_distributed:
                    paid_but_wallets_not_distributed.append(order)

            # C) Wallet tx should not exist on unpaid (guard)
            if pay_status != "paid":
                # Pilot guard (OPTION A):
                # Bloquant UNIQUEMENT si:
                # - payout driver
                # - credit/payout blanchisserie
                dangerous_tx = (
                    WalletTransaction.objects.filter(order=order)
                    .filter(
                        (
                            (models.Q(type="payout") & models.Q(wallet__owner_type="driver"))
                            |
                            (models.Q(type__in=("credit", "payout")) & models.Q(wallet__owner_type="laundry"))
                        )
                    )
                )
                if dangerous_tx.exists():
                    unpaid_with_wallet_tx.append(order)
            # SAFE FIX: sync payment status (only; never destructive)
            if do_fix and (not dry_run):
                # Only try to sync if an explicit method exists
                sync_fn = getattr(order, "sync_payment_status_from_payments", None)
                if callable(sync_fn):
                    try:
                        before = self._get_attr_first(order, ["payment_status"], default=None)
                        sync_fn()
                        # If sync_fn doesn't save, try to save
                        try:
                            order.save(update_fields=["payment_status"])
                        except Exception:
                            order.save()
                        after = self._get_attr_first(order, ["payment_status"], default=None)
                        if before != after:
                            fixed_count += 1
                    except Exception as e:
                        if verbose:
                            self.stdout.write(self.style.ERROR(f"Fix sync failed for order={order.id}: {e}"))

        # Summary
        self.stdout.write(self.style.SUCCESS(f"Orders scanned: {total}"))
        self.stdout.write("")

        def print_bucket(title, rows, formatter=None):
            self.stdout.write(self.style.ERROR(f"{title}: {len(rows)}"))
            if not rows:
                self.stdout.write("")
                return
            for i, item in enumerate(rows[:limit], start=1):
                if formatter:
                    formatter(i, item)
                else:
                    show_row(i, item, title)
            self.stdout.write("")

        print_bucket("❌ Paid without laundry", paid_without_laundry, lambda i, o: show_row(i, o, "paid_without_laundry"))
        print_bucket(
            "❌ Paid without delivery_partner",
            paid_without_delivery_partner,
            lambda i, o: show_row(i, o, "paid_without_delivery_partner"),
        )
        print_bucket("❌ Paid without legs", paid_without_legs, lambda i, o: show_row(i, o, "paid_without_legs"))
        print_bucket(
            "⚠️ Paid but wallets_distributed=False",
            paid_but_wallets_not_distributed,
            lambda i, o: show_row(i, o, "wallets_not_distributed"),
        )

        def fmt_inconsistent(i, tup):
            o, total_ttc, amount_paid = tup
            show_row(i, o, "inconsistent_payment_status", extra=f"total_ttc={total_ttc} amount_paid={amount_paid}")

        print_bucket("❌ Inconsistent payment status / amounts", inconsistent_payment_status, fmt_inconsistent)
        print_bucket(
            "❌ UNPAID with wallet transactions",
            unpaid_with_wallet_tx,
            lambda i, o: show_row(i, o, "unpaid_with_wallet_tx"),
        )

        blocking = (
            len(paid_without_laundry)
            + len(paid_without_delivery_partner)
            + len(paid_without_legs)
            + len(inconsistent_payment_status)
            + len(unpaid_with_wallet_tx)
        )

        if do_fix:
            self.stdout.write(self.style.WARNING(f"Fix mode: attempted SAFE sync on orders; changed payment_status: {fixed_count}"))
            self.stdout.write("")

        if blocking == 0:
            self.stdout.write(self.style.SUCCESS("✅ PILOT READY — 0 blocking issues"))
        else:
            self.stdout.write(self.style.ERROR(f"⛔ PILOT NOT READY — blocking issues: {blocking}"))

        # Non-zero exit is useful in CI, but we keep it simple (no hard exit).
