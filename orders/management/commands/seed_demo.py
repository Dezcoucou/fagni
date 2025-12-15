from django.core.management.base import BaseCommand
from django.apps import apps
from django.utils.text import slugify

class Command(BaseCommand):
    help = "Seed demo data (categories, services, laundries, drivers) for testing."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing demo rows before seeding.")

    def handle(self, *args, **opts):
        ServiceCategory = apps.get_model("orders", "ServiceCategory")
        ServiceItem     = apps.get_model("orders", "ServiceItem")
        LaundryPartner  = apps.get_model("partners", "LaundryPartner")
        DeliveryPartner = apps.get_model("partners", "DeliveryPartner")

        reset = opts["reset"]

        # --- Helpers ---
        def has_field(Model, name): 
            return any(f.name == name for f in Model._meta.get_fields())

        def set_if(obj, field, value):
            if has_field(obj.__class__, field):
                setattr(obj, field, value)

        def unique_slug(Model, base):
            s = slugify(base)[:45] or "item"
            if not Model.objects.filter(slug=s).exists():
                return s
            i = 2
            while True:
                ss = f"{s}-{i}"
                if not Model.objects.filter(slug=ss).exists():
                    return ss
                i += 1

        # --- Optional reset demo rows only (safe-ish) ---
        if reset:
            # On supprime uniquement ce qui ressemble à "Demo"
            ServiceItem.objects.filter(name__icontains="Demo").delete()
            ServiceCategory.objects.filter(name__icontains="Demo").delete()
            LaundryPartner.objects.filter(name__icontains="Demo").delete()
            DeliveryPartner.objects.filter(name__icontains="Demo").delete()

        self.stdout.write(self.style.SUCCESS("=== SEED_DEMO: start ==="))

        # =========================
        # 1) Categories
        # =========================
        categories = [
            "Lavage & Repassage",
            "Repassage seul",
            "Couettes & couvertures",
            "Retouche",
            "Cordonnerie",
        ]
        cat_objs = {}
        created_c = 0

        for name in categories:
            slug = slugify(name)
            obj = ServiceCategory.objects.filter(slug=slug).first()
            if not obj:
                obj = ServiceCategory.objects.create(name=name, slug=slug)
                created_c += 1
            cat_objs[name] = obj

        self.stdout.write(f"Categories: +{created_c} (total={ServiceCategory.objects.count()})")

        # =========================
        # 2) Services
        # =========================
        services = [
            ("Lavage & Repassage", "Chemise", 1000),
            ("Lavage & Repassage", "Pantalon", 1200),
            ("Lavage & Repassage", "T-shirt", 800),
            ("Repassage seul", "Chemise (repassage)", 700),
            ("Repassage seul", "Pantalon (repassage)", 800),
            ("Couettes & couvertures", "Couette 2 places", 3500),
            ("Couettes & couvertures", "Drap", 1000),
            ("Retouche", "Ourlet pantalon", 1500),
            ("Retouche", "Changement fermeture", 2500),
            ("Cordonnerie", "Cirage", 1000),
        ]

        created_s = 0
        # champs possibles côté ServiceItem (selon ton modèle)
        price_fields = ["default_price", "price", "unit_price", "base_price"]

        for cat_name, svc_name, price in services:
            cat = cat_objs[cat_name]
            obj = ServiceItem.objects.filter(name=svc_name, category=cat).first()
            if not obj:
                obj = ServiceItem(name=svc_name, category=cat)
                # slug (si présent)
                if has_field(ServiceItem, "slug"):
                    set_if(obj, "slug", unique_slug(ServiceItem, f"{cat_name}-{svc_name}"))
                # prix (selon champ existant)
                for pf in price_fields:
                    if has_field(ServiceItem, pf):
                        set_if(obj, pf, price)
                        break
                # actif
                set_if(obj, "is_active", True)
                obj.save()
                created_s += 1

        self.stdout.write(f"Services: +{created_s} (total={ServiceItem.objects.count()})")

        # =========================
        # 3) Laundries (partners)
        # =========================
        laundries = [
            ("Pressing Demo Cocody", "Cocody Angré", 5.371, -3.989),
            ("Pressing Demo Plateau", "Plateau", 5.323, -4.019),
            ("Pressing Demo Marcory", "Marcory", 5.292, -3.996),
        ]

        created_l = 0
        for name, address, lat, lng in laundries:
            obj = LaundryPartner.objects.filter(name=name).first()
            if not obj:
                obj = LaundryPartner(name=name)
                set_if(obj, "is_active", True)
                set_if(obj, "address", address)
                set_if(obj, "latitude", lat)
                set_if(obj, "longitude", lng)
                obj.save()
                created_l += 1

        self.stdout.write(f"Laundries: +{created_l} (total={LaundryPartner.objects.count()})")

        # =========================
        # 4) Drivers (partners)
        # =========================
        drivers = [
            ("Livreur Demo 1", "0700000001"),
            ("Livreur Demo 2", "0700000002"),
            ("Livreur Demo 3", "0700000003"),
        ]

        created_d = 0
        for name, phone in drivers:
            obj = DeliveryPartner.objects.filter(name=name).first()
            if not obj:
                obj = DeliveryPartner(name=name)
                set_if(obj, "is_active", True)
                # phone/mobile selon modèle
                if has_field(DeliveryPartner, "phone"):
                    set_if(obj, "phone", phone)
                elif has_field(DeliveryPartner, "mobile"):
                    set_if(obj, "mobile", phone)
                obj.save()
                created_d += 1

        self.stdout.write(f"Drivers: +{created_d} (total={DeliveryPartner.objects.count()})")

        self.stdout.write(self.style.SUCCESS("=== SEED_DEMO: done ==="))
