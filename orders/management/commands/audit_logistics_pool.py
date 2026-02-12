from decimal import Decimal, ROUND_HALF_UP
from django.core.management.base import BaseCommand
from django.db.models import Sum

from orders.models import Order
from wallets.models import WalletTransaction


def D(x):
    return Decimal(str(x or 0))


def FCFA(x) -> Decimal:
    """
    Normalise en FCFA (entier) pour éviter les faux positifs dus aux décimales.
    """
    return D(x).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


class Command(BaseCommand):
    help = "Audit cohérence pool logistique: Order vs Legs vs Payouts driver (FCFA-safe)."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=500)
        parser.add_argument("--ids", type=str, default="")
        parser.add_argument("--fix", action="store_true")
        parser.add_argument("--verbose", action="store_true")

    def handle(self, *args, **opts):
        limit = opts["limit"]
        ids = [int(x) for x in opts["ids"].split(",") if x.strip().isdigit()]
        do_fix = bool(opts["fix"])
        verbose = bool(opts.get("verbose"))

        qs = Order.objects.order_by("-id")
        if ids:
            qs = qs.filter(id__in=ids)
        qs = qs[:limit]

        bad_paid_vs_dt = 0
        bad_legs_vs_dt = 0
        bad_pool_invariant = 0
        fixed = 0

        for o in qs:
            # On compare en FCFA (arrondi) pour être cohérent avec logistic_margin souvent entier
            df = FCFA(getattr(o, "delivery_fee", 0))
            dt = FCFA(getattr(o, "amount_driver_partner", 0))
            mt = FCFA(getattr(o, "logistic_margin", 0))

            try:
                legs = list(o.legs.exclude(status="canceled"))
            except Exception:
                legs = []

            legs_driver = FCFA(sum([D(getattr(l, "driver_amount", 0)) for l in legs]) if legs else D(0))

            paid = WalletTransaction.objects.filter(
                order=o, type="payout", direction="in", wallet__owner_type="driver"
            ).aggregate(s=Sum("amount")).get("s") or 0
            paid = FCFA(paid)

            # 1) paid ne doit jamais dépasser dt (sinon dt faux)
            if paid > 0 and paid > dt:
                bad_paid_vs_dt += 1
                if verbose:
                    print(f"[bad_paid_vs_dt] order={o.id} df={df} dt={dt} mt={mt} paid={paid} legs_driver={legs_driver}")

            # 2) si paid > 0, legs_driver doit matcher dt
            if paid > 0 and legs_driver != dt:
                bad_legs_vs_dt += 1
                if verbose:
                    print(f"[bad_legs_vs_dt] order={o.id} df={df} dt={dt} mt={mt} paid={paid} legs_driver={legs_driver}")

            # 3) invariant pool : si df > 0, alors mt = max(df-dt, 0) et dt <= df
            if df > 0:
                expected_mt = df - dt
                if expected_mt < 0:
                    expected_mt = FCFA(0)
                if dt > df or mt != expected_mt:
                    bad_pool_invariant += 1
                    if verbose:
                        print(f"[bad_pool_invariant] order={o.id} df={df} dt={dt} mt={mt} expected_mt={expected_mt} paid={paid} legs_driver={legs_driver}")

            if do_fix:
                # Fix invariant pool même si paid == 0
                new_dt = dt

                # si legs_driver existe => on s'aligne dessus (meilleure source)
                if legs_driver > 0:
                    new_dt = legs_driver

                # legacy: si df=0 mais legs/payout >0 => on remet df = new_dt
                if df <= 0 and new_dt > 0:
                    df = new_dt
                    o.delivery_fee = df

                # dt ne doit jamais dépasser df
                if df > 0 and new_dt > df:
                    new_dt = df

                new_mt = (df - new_dt) if df > 0 else FCFA(0)
                if new_mt < 0:
                    new_mt = FCFA(0)

                changed = False
                if FCFA(getattr(o, "amount_driver_partner", 0)) != FCFA(new_dt):
                    o.amount_driver_partner = new_dt
                    changed = True
                if FCFA(getattr(o, "driver_logistic_cost", 0)) != FCFA(new_dt):
                    o.driver_logistic_cost = new_dt
                    changed = True
                if FCFA(getattr(o, "logistic_margin", 0)) != FCFA(new_mt):
                    o.logistic_margin = new_mt
                    changed = True

                fields = ["amount_driver_partner", "driver_logistic_cost", "logistic_margin"]
                if getattr(o, "delivery_fee", None) is not None:
                    fields.append("delivery_fee")

                if changed:
                    o.save(update_fields=fields)
                    fixed += 1

        print(f"bad_paid_vs_dt={bad_paid_vs_dt}")
        print(f"bad_legs_vs_dt={bad_legs_vs_dt}")
        print(f"bad_pool_invariant={bad_pool_invariant}")
        print(f"{'FIXED=' + str(fixed) if do_fix else 'AUDIT ONLY (use --fix)'}")
