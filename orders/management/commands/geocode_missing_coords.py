from django.core.management.base import BaseCommand
from orders.models import Order
from orders.utils.geocoding import ensure_order_geocoded


class Command(BaseCommand):
    help = "Geocode pickup/delivery coords for orders missing GPS."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **opts):
        limit = opts["limit"]
        dry = opts["dry_run"]

        qs = Order.objects.select_related("customer", "laundry_partner").order_by("-created_at")

        done = 0
        changed = 0

        for o in qs[:limit]:
            # on tente seulement si manque pickup OU delivery
            miss_pickup = not (getattr(o, "pickup_latitude", None) or getattr(o, "pickup_lat", None))
            miss_delivery = not (getattr(o, "delivery_latitude", None) or getattr(o, "delivery_lat", None))

            if not (miss_pickup or miss_delivery):
                continue

            done += 1
            if dry:
                self.stdout.write(f"[DRY] would geocode order #{o.id}")
                continue

            if ensure_order_geocoded(o, save=True):
                changed += 1
                self.stdout.write(self.style.SUCCESS(f"OK geocoded order #{o.id}"))
            else:
                self.stdout.write(self.style.WARNING(f"NO result for order #{o.id}"))

        self.stdout.write(self.style.SUCCESS(f"Scanned: {done}, Updated: {changed}"))
