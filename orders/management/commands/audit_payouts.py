from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models import Sum

from orders.models import DeliveryLeg
from orders.service_layer.payouts import trigger_driver_payout_for_leg


class Command(BaseCommand):
    help = (
        "Audit payouts legs (dry-run par défaut). "
        "Options: --fix-missing, --reconcile, --fix-flags, --reverse-unpaid, --fix-duplicates"
    )

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=200, help="Limiter le nombre d'anomalies affichées")
        parser.add_argument(
            "--only",
            choices=["paid", "unpaid", "all"],
            default="all",
            help="Filtrer sur les commandes paid/unpaid",
        )
        parser.add_argument(
            "--fix-missing",
            action="store_true",
            help="Crée les payouts manquants (leg done + order paid) via trigger_driver_payout_for_leg",
        )
        parser.add_argument(
            "--reconcile",
            action="store_true",
            help="Ajuste si net payout != driver_amount (crée tx adjustment) — PAID ONLY",
        )
        parser.add_argument(
            "--fix-flags",
            action="store_true",
            help="Synchronise driver_wallet_credited si payouts legs existent",
        )
        parser.add_argument(
            "--reverse-unpaid",
            action="store_true",
            help="Annule (ramène net à 0) les payouts/adjustments existants sur des legs DONE dont la commande est UNPAID",
        )
        parser.add_argument(
            "--fix-duplicates",
            action="store_true",
            help="Corrige les doublons payout_in par leg (PAID ONLY) via tx adjustment OUT (ne supprime rien)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Forcer dry-run (ne modifie rien)")
        parser.add_argument("--verbose", action="store_true", help="Détails par leg")

    def _dec(self, x):
        return Decimal(str(x or 0))

    def handle(self, *args, **opts):
        from django.apps import apps
        from wallets.services import get_or_create_wallet_for_delivery_partner, credit_wallet, debit_wallet

        WalletTransaction = apps.get_model("wallets", "WalletTransaction")

        any_fix = bool(
            opts["fix_missing"]
            or opts["reconcile"]
            or opts["fix_flags"]
            or opts["reverse_unpaid"]
            or opts.get("fix_duplicates")
        )
        dry_run = bool(opts["dry_run"]) or (not any_fix)

        only = opts["only"]
        limit = opts["limit"]
        verbose = bool(opts["verbose"])

        self.stdout.write(self.style.WARNING(f"Audit payouts legs — dry_run={dry_run}"))

        # Legs done
        legs_qs = DeliveryLeg.objects.filter(status="done").select_related("order", "driver").order_by("id")

        if only in ("paid", "unpaid"):
            legs_qs = legs_qs.filter(order__payment_status=("paid" if only == "paid" else "unpaid"))

        total_legs_done = legs_qs.count()

        missing = []
        mismatch = []
        unpaid_has_payout = []
        duplicates = []

        # Helper: net payout sur une jambe
        def net_for_leg(leg):
            paid_in = (
                WalletTransaction.objects.filter(leg=leg, type="payout", direction="in")
                .aggregate(s=Sum("amount"))["s"]
                or Decimal("0")
            )
            adj_in = (
                WalletTransaction.objects.filter(leg=leg, type="adjustment", direction="in")
                .aggregate(s=Sum("amount"))["s"]
                or Decimal("0")
            )
            adj_out = (
                WalletTransaction.objects.filter(leg=leg, type="adjustment", direction="out")
                .aggregate(s=Sum("amount"))["s"]
                or Decimal("0")
            )
            net = self._dec(paid_in) + self._dec(adj_in) - self._dec(adj_out)
            return self._dec(paid_in), self._dec(adj_in), self._dec(adj_out), self._dec(net)


        # Helper: upsert adjustment (1 IN + 1 OUT max par leg, sinon violation UNIQUE)

        def upsert_adjustment(wallet, order, leg, direction: str, amount: Decimal, description: str):

            amount = self._dec(amount)

            if amount <= 0:

                return None


            existing = WalletTransaction.objects.filter(

                wallet=wallet, order=order, leg=leg, type="adjustment", direction=direction

            ).order_by("id").first()


            if existing:

                old = self._dec(existing.amount)

                delta = amount - old

                if delta == 0:

                    return existing


                # Ajuster le solde wallet du delta

                if direction == "in":

                    wallet.balance = self._dec(wallet.balance) + delta

                else:

                    wallet.balance = self._dec(wallet.balance) - delta


                wallet.save(update_fields=["balance", "updated_at"])

                existing.amount = amount

                existing.description = description or existing.description

                existing.save(update_fields=["amount", "description"])

                return existing


            # Sinon create normal (respecte la contrainte UNIQUE)

            if direction == "in":

                return credit_wallet(

                    wallet,

                    amount,

                    description=description,

                    order=order,

                    leg=leg,

                    tx_type="adjustment",

                )

            return debit_wallet(

                wallet,

                amount,

                description=description,

                order=order,

                leg=leg,

                tx_type="adjustment",

            )



        # Scan legs
        for leg in legs_qs.iterator():
            order = getattr(leg, "order", None)
            driver = getattr(leg, "driver", None)
            if not order or not driver:
                continue

            target = self._dec(getattr(leg, "driver_amount", 0))
            if target <= 0:
                continue

            paid_in, adj_in, adj_out, net = net_for_leg(leg)
            diff = target - net

            payout_qs = WalletTransaction.objects.filter(leg=leg, type="payout", direction="in")
            has_payout = payout_qs.exists()
            payout_count = payout_qs.count()
            has_dup = payout_count > 1

            if getattr(order, "payment_status", None) != "paid":
                # anomalie: unpaid mais payout/adjust exists (net != 0)
                if has_payout or net != 0:
                    unpaid_has_payout.append((order, leg, target, net, diff))
                continue

            # order paid
            if has_dup:
                duplicates.append((order, leg, target, net, diff))

            if not has_payout:
                missing.append((order, leg, target, net, diff))
            elif diff != 0:
                mismatch.append((order, leg, target, net, diff))

        # Résumé
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Legs done analysées: {total_legs_done}"))
        self.stdout.write(self.style.ERROR(f"Missing payouts (paid+done sans payout): {len(missing)}"))
        self.stdout.write(self.style.ERROR(f"Mismatch net!=target (paid+done): {len(mismatch)}"))
        self.stdout.write(self.style.ERROR(f"UNPAID avec payout/net != 0: {len(unpaid_has_payout)}"))
        self.stdout.write(self.style.ERROR(f"Duplicate payouts (paid+done, payout_in>1): {len(duplicates)}"))
        self.stdout.write("")

        def show_list(title, rows):
            self.stdout.write(self.style.WARNING(title))
            for i, (order, leg, target, net, diff) in enumerate(rows[:limit], start=1):
                self.stdout.write(
                    f"{i:03d} | order={order.id} {getattr(order,'code', '')} "
                    f"pay={getattr(order,'payment_status', None)} status={getattr(order,'status', None)} "
                    f"| leg={leg.id} type={getattr(leg,'leg_type','')} driver={getattr(getattr(leg,'driver',None), 'id', None)} "
                    f"| target={target} net={net} diff={diff}"
                )
                if verbose:
                    paid_in, adj_in, adj_out, net2 = net_for_leg(leg)
                    self.stdout.write(
                        f"      breakdown: payout_in={paid_in} adj_in={adj_in} adj_out={adj_out} net={net2}"
                    )
                    # Bonus: compter les payouts IN
                    c = WalletTransaction.objects.filter(leg=leg, type="payout", direction="in").count()
                    self.stdout.write(f"      payout_in_count={c}")
            self.stdout.write("")

        if missing:
            show_list(">> Missing payouts:", missing)
        if mismatch:
            show_list(">> Mismatch payouts:", mismatch)
        if unpaid_has_payout:
            show_list(">> UNPAID mais payouts/adjust présents:", unpaid_has_payout)
        if duplicates:
            show_list(">> DUPLICATE payouts (payout_in>1):", duplicates)

        # FIXES
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry-run: aucune modification effectuée."))
            return

        # 0) Reverse unpaid nets -> 0
        if opts["reverse_unpaid"] and unpaid_has_payout:
            self.stdout.write(self.style.WARNING("Reverse UNPAID nets -> 0 (adjustment)..."))
            fixed = 0
            for order, leg, target, net, diff in unpaid_has_payout:
                # recalcul net exact à l'instant T
                paid_in, adj_in, adj_out, net2 = net_for_leg(leg)
                if net2 == 0:
                    continue
                wallet = get_or_create_wallet_for_delivery_partner(leg.driver)

                # Si net > 0 : trop crédité -> OUT
                if net2 > 0:
                    debit_wallet(
                        wallet,
                        net2,
                        description=f"Reverse UNPAID payout leg #{leg.id} (audit)",
                        order=order,
                        leg=leg,
                        tx_type="adjustment",
                    )
                # Si net < 0 : trop débité -> IN (rare, mais on ramène à 0)
                else:
                    credit_wallet(
                        wallet,
                        -net2,
                        description=f"Reverse UNPAID negative net leg #{leg.id} (audit)",
                        order=order,
                        leg=leg,
                        tx_type="adjustment",
                    )

                fixed += 1
            self.stdout.write(self.style.SUCCESS(f"UNPAID reversed: {fixed}/{len(unpaid_has_payout)}"))
            self.stdout.write("")

        # 1) Fix missing payouts (PAID ONLY - guard)
        if opts["fix_missing"] and missing:
            self.stdout.write(self.style.WARNING("Fix missing payouts..."))
            fixed = 0
            for order, leg, target, net, diff in missing:
                if getattr(order, "payment_status", None) != "paid":
                    continue
                tx = trigger_driver_payout_for_leg(leg)
                if tx:
                    fixed += 1
            self.stdout.write(self.style.SUCCESS(f"Missing payouts créés: {fixed}/{len(missing)}"))
            self.stdout.write("")
        # 2) Reconcile mismatch (PAID ONLY - guard)
        if opts["reconcile"] and mismatch:
            self.stdout.write(self.style.WARNING("Reconcile mismatch (adjustments)..."))

            from django.db import transaction

            def upsert_adjustment(wallet, order, leg, direction, amount, *, description=""):
                """Ajoute (cumul) UNE adjustment par (wallet, order, leg, direction)."""
                amount = self._dec(amount)
                if amount <= 0:
                    return None

                with transaction.atomic():
                    # tentative de lock wallet (sqlite ignore, pg ok)
                    try:
                        w = type(wallet).objects.select_for_update().get(id=wallet.id)
                    except Exception:
                        w = wallet

                    existing = WalletTransaction.objects.filter(
                        wallet=w,
                        order=order,
                        leg=leg,
                        type="adjustment",
                        direction=direction,
                    ).order_by("id").first()

                    if existing:
                        old = self._dec(existing.amount)
                        new_amount = old + amount  # ✅ cumul
                        if new_amount < 0:
                            new_amount = self._dec(0)

                        # wallet.balance bouge du delta (= amount effectivement ajoutée)
                        if direction == "in":
                            w.balance = self._dec(w.balance) + amount
                        else:
                            w.balance = self._dec(w.balance) - amount
                        w.save(update_fields=["balance", "updated_at"])

                        existing.amount = new_amount
                        if description:
                            existing.description = description
                        existing.save(update_fields=["amount", "description"])
                        return existing

                    # create new adjustment (protégé par la contrainte unique)
                    if direction == "in":
                        w.balance = self._dec(w.balance) + amount
                    else:
                        w.balance = self._dec(w.balance) - amount
                    w.save(update_fields=["balance", "updated_at"])

                    tx = WalletTransaction.create_tx(
                        wallet=w,
                        order=order,
                        leg=leg,
                        type="adjustment",
                        direction=direction,
                        amount=amount,
                        description=description or "",
                        idempotency_key=f"audit_adj:{order.id}:{leg.id}:{direction}",
                        allow_orphan=False,
                    )
                    return tx

            fixed = 0
            for order, leg, target, net, diff in mismatch:
                if getattr(order, "payment_status", None) != "paid":
                    continue

                paid_in, adj_in, adj_out, net2 = net_for_leg(leg)
                diff2 = target - net2
                if diff2 == 0:
                    continue

                wallet = get_or_create_wallet_for_delivery_partner(leg.driver)
                if diff2 > 0:
                    tx = upsert_adjustment(
                        wallet,
                        order,
                        leg,
                        "in",
                        diff2,
                        description=f"Audit reconcile leg #{leg.id} (diff +)",
                    )
                else:
                    tx = upsert_adjustment(
                        wallet,
                        order,
                        leg,
                        "out",
                        -diff2,
                        description=f"Audit reconcile leg #{leg.id} (diff -)",
                    )
                if tx:
                    fixed += 1

            self.stdout.write(self.style.SUCCESS(f"Mismatch reconciled: {fixed}/{len(mismatch)}"))
            self.stdout.write("")

        # 3) Fix flags cache
        if opts["fix_flags"]:
            self.stdout.write(self.style.WARNING("Fix flags driver_wallet_credited..."))
            from orders.models import Order

            updated = 0
            for o in Order.objects.all().iterator():
                has_leg_payout = WalletTransaction.objects.filter(
                    order=o, leg__isnull=False, type="payout", direction="in"
                ).exists()
                if has_leg_payout and not getattr(o, "driver_wallet_credited", False):
                    o.driver_wallet_credited = True
                    o.save(update_fields=["driver_wallet_credited"])
                    updated += 1
            self.stdout.write(self.style.SUCCESS(f"driver_wallet_credited mis à jour: {updated}"))
            self.stdout.write("")

        # 4) Fix duplicates (PAID ONLY) — ajuste pour ramener net à target
        if opts.get("fix_duplicates") and duplicates:
            self.stdout.write(self.style.WARNING("Fix duplicates payouts (adjustments OUT)..."))
            fixed = 0
            for order, leg, target, net, diff in duplicates:
                if getattr(order, "payment_status", None) != "paid":
                    continue

                paid_in, adj_in, adj_out, net2 = net_for_leg(leg)
                excess = self._dec(net2) - self._dec(target)
                if excess <= 0:
                    continue

                wallet = get_or_create_wallet_for_delivery_partner(leg.driver)
                debit_wallet(
                    wallet,
                    excess,
                    description=f"Fix DUP payout leg #{leg.id} (excess {excess})",
                    order=order,
                    leg=leg,
                    tx_type="adjustment",
                )
                fixed += 1

            self.stdout.write(self.style.SUCCESS(f"Duplicates fixed (excess debited): {fixed}/{len(duplicates)}"))
            self.stdout.write("")

        self.stdout.write(self.style.SUCCESS("OK: audit + corrections terminés."))
