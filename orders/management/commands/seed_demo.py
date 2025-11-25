from django.core.management.base import BaseCommand
from django.utils import timezone
from decimal import Decimal
import random

from orders.models import (
    Customer,
    Order,
    OrderItem,
    ServiceCategory,
    ServiceItem,
)
from partners.models import LaundryPartner, DeliveryPartner


class Command(BaseCommand):
    help = "Crée des données de démonstration FAGNI (clients, partenaires, services, commandes)."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("=== SEED DEMO DATA FAGNI ==="))

        existing_orders = Order.objects.count()
        if existing_orders > 0:
            self.stdout.write(
                self.style.WARNING(
                    f"Il y a déjà {existing_orders} commande(s) en base. "
                    "On ajoute juste des données de démo en plus, sans tout effacer."
                )
            )

        # 1) Services & catégories
        self._seed_services()

        # 2) Partenaires
        self._seed_partners()

        # 3) Clients
        customers = self._seed_customers()

        # 4) Commandes
        self._seed_orders(customers)

        self.stdout.write(self.style.SUCCESS("✔ Données de démo FAGNI créées avec succès."))

    # ------------------------------------------------------------------
    # 1) Services / catégories
    # ------------------------------------------------------------------
    def _seed_services(self):
        if ServiceCategory.objects.exists() and ServiceItem.objects.exists():
            self.stdout.write("Services déjà présents, on ne recrée pas.")
            return

        self.stdout.write("Création de catégories et services de démo...")

        cat_linge = ServiceCategory.objects.create(name="Linge quotidien")
        cat_repassage = ServiceCategory.objects.create(name="Repassage")
        cat_special = ServiceCategory.objects.create(name="Articles spéciaux")

        # On ne met PAS de champ de prix ici (ton modèle n'a pas 'base_price')
        services_data = [
            (cat_linge, "Chemise"),
            (cat_linge, "Pantalon"),
            (cat_linge, "T-shirt"),
            (cat_repassage, "Repassage chemise"),
            (cat_repassage, "Repassage pantalon"),
            (cat_special, "Robe de soirée"),
            (cat_special, "Costume complet"),
        ]

        for cat, name in services_data:
            ServiceItem.objects.create(
                category=cat,
                name=name,
                is_active=True,
            )

        self.stdout.write(self.style.SUCCESS("✔ Services de démo créés."))

    # ------------------------------------------------------------------
    # 2) Partenaires blanchisseries / livreurs
    # ------------------------------------------------------------------
    def _seed_partners(self):
        if LaundryPartner.objects.exists() or DeliveryPartner.objects.exists():
            self.stdout.write("Partenaires déjà présents, on ne recrée pas.")
            return

        self.stdout.write("Création de partenaires de démo...")

        LaundryPartner.objects.create(name="Blanchisserie Cocody", city="Abidjan")
        LaundryPartner.objects.create(name="Blanchisserie Yopougon", city="Abidjan")
        LaundryPartner.objects.create(name="Blanchisserie Riviera", city="Abidjan")

        DeliveryPartner.objects.create(name="Livreur Alpha")
        DeliveryPartner.objects.create(name="Livreur Bravo")
        DeliveryPartner.objects.create(name="Livreur Charlie")

        self.stdout.write(self.style.SUCCESS("✔ Partenaires de démo créés."))

    # ------------------------------------------------------------------
    # 3) Clients
    # ------------------------------------------------------------------
    def _seed_customers(self):
        self.stdout.write("Création de clients de démo...")

        base_clients = [
            ("Client Démo 1", "0101010101", "Cocody Angré"),
            ("Client Démo 2", "0202020202", "Plateau"),
            ("Client Démo 3", "0303030303", "Yopougon"),
            ("Client Démo 4", "0404040404", "Marcory"),
            ("Client Démo 5", "0505050505", "Riviera 2"),
        ]

        customers = []
        for name, phone, address in base_clients:
            c, created = Customer.objects.get_or_create(
                phone=phone,
                defaults={
                    "name": name,
                    "address": address,
                },
            )
            c.name = name
            c.address = address
            c.save()
            customers.append(c)

        self.stdout.write(self.style.SUCCESS(f"✔ {len(customers)} clients de démo prêts."))
        return customers

    # ------------------------------------------------------------------
    # 4) Commandes + items
    # ------------------------------------------------------------------
    def _seed_orders(self, customers):
        if not customers:
            self.stdout.write(self.style.WARNING("Aucun client dispo pour créer des commandes."))
            return

        services = list(ServiceItem.objects.filter(is_active=True))
        laundries = list(LaundryPartner.objects.all())
        drivers = list(DeliveryPartner.objects.all())

        if not services:
            self.stdout.write(
                self.style.WARNING("Pas de services actifs, impossible de créer des commandes.")
            )
            return

        self.stdout.write("Création de commandes de démo...")

        # Petit mapping local pour donner des prix par nom de service
        price_map = {
            "Chemise": 500,
            "Pantalon": 800,
            "T-shirt": 400,
            "Repassage chemise": 300,
            "Repassage pantalon": 400,
            "Robe de soirée": 2500,
            "Costume complet": 2000,
        }

        nb_orders = 20
        now = timezone.now()
        created_count = 0

        for i in range(nb_orders):
            customer = random.choice(customers)
            laundry = random.choice(laundries) if laundries else None
            driver = random.choice(drivers) if drivers else None

            status = random.choice(["pending", "in_progress", "done"])
            created_at = now - timezone.timedelta(days=random.randint(0, 14))

            order = Order.objects.create(
                customer=customer,
                status=status,
                laundry_partner=laundry,
                delivery_partner=driver,
            )

            # on force une date de création réaliste
            try:
                Order.objects.filter(pk=order.pk).update(created_at=created_at)
                order.refresh_from_db()
            except Exception:
                pass

            nb_items = random.randint(2, 5)
            total_presta = Decimal("0")

            for _ in range(nb_items):
                service = random.choice(services)
                qty = random.randint(1, 5)

                # Prix basé sur le nom du service, sinon 500 par défaut
                base_price = price_map.get(service.name, 500)
                unit_price = Decimal(str(base_price))

                item = OrderItem.objects.create(
                    order=order,
                    service=service,
                    designation=service.name,
                    quantity=qty,
                    unit_price=unit_price,
                )
                total_presta += item.total

            # On alimente quelques champs si présents sur ton modèle Order
            if hasattr(order, "total_ht") and order.total_ht is None:
                order.total_ht = total_presta

            if hasattr(order, "total_ttc") and order.total_ttc is None:
                order.total_ttc = total_presta

            if hasattr(order, "service_fee") and (order.service_fee is None or order.service_fee == 0):
                service_fee = total_presta * Decimal("0.05")
                if service_fee < Decimal("500") and total_presta > 0:
                    service_fee = Decimal("500")
                order.service_fee = service_fee

            if hasattr(order, "delivery_fee") and (order.delivery_fee is None or order.delivery_fee == 0):
                order.delivery_fee = Decimal("1000") if total_presta > 0 else Decimal("0")

            if hasattr(order, "amount_paid") and order.amount_paid is None:
                if status == "done" and random.random() < 0.7:
                    total_client = (
                        (order.total_ttc or Decimal("0"))
                        + (order.service_fee or Decimal("0"))
                        + (order.delivery_fee or Decimal("0"))
                    )
                    order.amount_paid = total_client
                else:
                    order.amount_paid = Decimal("0")

            if hasattr(order, "amount_due") and order.amount_due is None:
                total_client = (
                    (order.total_ttc or Decimal("0"))
                    + (order.service_fee or Decimal("0"))
                    + (order.delivery_fee or Decimal("0"))
                )
                order.amount_due = total_client - (order.amount_paid or Decimal("0"))

            if hasattr(order, "distance_km") and order.distance_km is None:
                order.distance_km = Decimal(str(random.choice([3, 5, 7, 10])))

            if hasattr(order, "driver_logistic_cost") and order.driver_logistic_cost is None:
                if getattr(order, "distance_km", None):
                    order.driver_logistic_cost = order.distance_km * Decimal("150")

            if hasattr(order, "logistic_margin") and order.logistic_margin is None:
                if getattr(order, "delivery_fee", None) is not None and getattr(order, "driver_logistic_cost", None) is not None:
                    order.logistic_margin = (order.delivery_fee or 0) - (order.driver_logistic_cost or 0)

            if status == "done" and hasattr(order, "delivered_time") and order.delivered_time is None:
                order.delivered_time = order.created_at + timezone.timedelta(hours=24)

            order.save()
            created_count += 1

        self.stdout.write(self.style.SUCCESS(f"✔ {created_count} commandes de démo créées."))
