from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import Address
from services.models import ServiceCategory, Service, ServiceOption
from pricing.models import PricingRule


class Command(BaseCommand):
    help = "Seed de base pour FAGNI V2"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Démarrage du seed FAGNI V2..."))

        # =========================
        # 1. Catégories de services
        # =========================
        categories = [
            {
                "code": "pressing",
                "name": "Pressing",
                "description": "Services de lavage, séchage, repassage et entretien textile.",
            },
            {
                "code": "retouche",
                "name": "Retouche",
                "description": "Services de couture, ajustement et reprise.",
            },
            {
                "code": "cordonnerie",
                "name": "Cordonnerie",
                "description": "Entretien, réparation et nettoyage des chaussures et accessoires cuir.",
            },
            {
                "code": "logistique",
                "name": "Logistique",
                "description": "Collecte, livraison et transport de proximité.",
            },
        ]

        created_categories = {}
        for data in categories:
            obj, created = ServiceCategory.objects.get_or_create(
                code=data["code"],
                defaults={
                    "name": data["name"],
                    "description": data["description"],
                    "is_active": True,
                },
            )
            created_categories[data["code"]] = obj
            if created:
                self.stdout.write(self.style.SUCCESS(f"Catégorie créée : {obj.name}"))
            else:
                self.stdout.write(f"Catégorie déjà existante : {obj.name}")

        # =========================
        # 2. Services
        # =========================
        services = [
            {
                "code": "pressing_bag",
                "category": "pressing",
                "name": "Pressing par sac",
                "description": (
                    "Traitement du linge selon une formule de sac FAGNI."
                ),
                "primary_engine": "pickup_return",
                "requires_partner": True,
                "requires_logistics": True,
                "requires_weighing": False,
                "requires_appointment": False,
                "requires_quote": False,
                "requires_asset": False,
                "requires_otp": False,
                "requires_signature": False,
                "pricing_mode": "bag",
                "default_sla_hours": 48,
            },
            {
                "code": "pressing_article",
                "category": "pressing",
                "name": "Pressing par article",
                "description": "Traitement des vêtements à l’unité.",
                "requires_partner": True,
                "requires_logistics": True,
                "requires_weighing": False,
                "pricing_mode": "per_item",
                "default_sla_hours": 48,
            },
            {
                "code": "pressing_kilo",
                "category": "pressing",
                "name": "Pressing au kilo",
                "description": "Traitement du linge au kilo.",
                "requires_partner": True,
                "requires_logistics": True,
                "requires_weighing": True,
                "pricing_mode": "per_kg",
                "default_sla_hours": 48,
            },
            {
                "code": "repassage",
                "category": "pressing",
                "name": "Repassage",
                "description": "Service de repassage simple ou premium.",
                "requires_partner": True,
                "requires_logistics": True,
                "requires_weighing": False,
                "pricing_mode": "per_item",
                "default_sla_hours": 24,
            },
            {
                "code": "retouche_simple",
                "category": "retouche",
                "name": "Retouche simple",
                "description": "Ourlet, reprise simple, ajustement mineur.",
                "requires_partner": True,
                "requires_logistics": True,
                "requires_weighing": False,
                "pricing_mode": "fixed",
                "default_sla_hours": 72,
            },
            {
                "code": "cordonnerie_standard",
                "category": "cordonnerie",
                "name": "Cordonnerie standard",
                "description": "Réparation et entretien standard des chaussures.",
                "requires_partner": True,
                "requires_logistics": True,
                "requires_weighing": False,
                "pricing_mode": "fixed",
                "default_sla_hours": 72,
            },
            {
                "code": "collecte_livraison",
                "category": "logistique",
                "name": "Collecte / Livraison",
                "description": "Service logistique simple sans transformation produit.",
                "requires_partner": False,
                "requires_logistics": True,
                "requires_weighing": False,
                "pricing_mode": "fixed",
                "default_sla_hours": 24,
            },
        ]

        created_services = {}
        for data in services:
            obj, created = Service.objects.get_or_create(
                code=data["code"],
                defaults={
                    "category": created_categories[data["category"]],
                    "name": data["name"],
                    "description": data["description"],
                    "is_active": True,
                    "primary_engine": data.get(
                        "primary_engine",
                        Service.ENGINE_PICKUP_RETURN,
                    ),
                    "requires_partner": data["requires_partner"],
                    "requires_logistics": data["requires_logistics"],
                    "requires_weighing": data["requires_weighing"],
                    "requires_appointment": data.get(
                        "requires_appointment",
                        False,
                    ),
                    "requires_quote": data.get(
                        "requires_quote",
                        False,
                    ),
                    "requires_asset": data.get(
                        "requires_asset",
                        False,
                    ),
                    "requires_otp": data.get(
                        "requires_otp",
                        False,
                    ),
                    "requires_signature": data.get(
                        "requires_signature",
                        False,
                    ),
                    "pricing_mode": data["pricing_mode"],
                    "default_sla_hours": data["default_sla_hours"],
                },
            )
            created_services[data["code"]] = obj
            if created:
                self.stdout.write(self.style.SUCCESS(f"Service créé : {obj.name}"))
            else:
                self.stdout.write(f"Service déjà existant : {obj.name}")

        # =========================
        # 3. Options de services
        # =========================
        service_options = [
            {
                "service_code": "pressing_article",
                "code": "express",
                "name": "Express",
                "description": "Traitement prioritaire",
                "extra_price_type": "fixed",
                "extra_price_value": Decimal("1000"),
            },
            {
                "service_code": "pressing_article",
                "code": "detachage",
                "name": "Détachage",
                "description": "Traitement spécial des taches",
                "extra_price_type": "fixed",
                "extra_price_value": Decimal("500"),
            },
            {
                "service_code": "pressing_kilo",
                "code": "express",
                "name": "Express",
                "description": "Traitement prioritaire au kilo",
                "extra_price_type": "percent",
                "extra_price_value": Decimal("15"),
            },
            {
                "service_code": "repassage",
                "code": "pliage_premium",
                "name": "Pliage premium",
                "description": "Finition premium et présentation soignée",
                "extra_price_type": "fixed",
                "extra_price_value": Decimal("500"),
            },
        ]

        for data in service_options:
            service = created_services[data["service_code"]]
            obj, created = ServiceOption.objects.get_or_create(
                service=service,
                code=data["code"],
                defaults={
                    "name": data["name"],
                    "description": data["description"],
                    "extra_price_type": data["extra_price_type"],
                    "extra_price_value": data["extra_price_value"],
                    "is_active": True,
                },
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Option créée : {service.name} / {obj.name}"))
            else:
                self.stdout.write(f"Option déjà existante : {service.name} / {obj.name}")

        # =========================
        # 4. Adresses de test
        # =========================
        addresses = [
            {
                "label": "Client test Cocody",
                "contact_name": "Client Test 1",
                "phone": "0700000001",
                "city": "Abidjan",
                "commune": "Cocody",
                "district": "2 Plateaux",
                "street": "Rue des Jardins",
                "landmark": "Près du carrefour",
                "full_address": "Cocody 2 Plateaux, Rue des Jardins, près du carrefour",
                "instructions": "Appeler avant arrivée",
                "is_default": False,
            },
            {
                "label": "Client test Yopougon",
                "contact_name": "Client Test 2",
                "phone": "0700000002",
                "city": "Abidjan",
                "commune": "Yopougon",
                "district": "Siporex",
                "street": "Voie principale",
                "landmark": "À côté de la pharmacie",
                "full_address": "Yopougon Siporex, voie principale, à côté de la pharmacie",
                "instructions": "",
                "is_default": False,
            },
            {
                "label": "Hub FAGNI test",
                "contact_name": "Opérations FAGNI",
                "phone": "0700000099",
                "city": "Abidjan",
                "commune": "Cocody",
                "district": "Riviera",
                "street": "Rue du Hub",
                "landmark": "Immeuble FAGNI",
                "full_address": "Cocody Riviera, Rue du Hub, Immeuble FAGNI",
                "instructions": "Point central de test",
                "is_default": False,
            },
        ]

        for data in addresses:
            obj, created = Address.objects.get_or_create(
                label=data["label"],
                full_address=data["full_address"],
                defaults=data,
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Adresse créée : {obj.label}"))
            else:
                self.stdout.write(f"Adresse déjà existante : {obj.label}")

        # =========================
        # 5. Règles tarifaires de base
        # =========================
        pricing_rules = [
            {
                "service_code": "pressing_article",
                "rule_type": "per_item",
                "label": "Prix chemise standard",
                "value": Decimal("1000"),
                "priority": 10,
            },
            {
                "service_code": "pressing_kilo",
                "rule_type": "per_kg",
                "label": "Prix pressing au kilo",
                "value": Decimal("1500"),
                "priority": 10,
            },
            {
                "service_code": "repassage",
                "rule_type": "per_item",
                "label": "Prix repassage unitaire",
                "value": Decimal("500"),
                "priority": 10,
            },
            {
                "service_code": "collecte_livraison",
                "rule_type": "fixed_fee",
                "label": "Frais logistiques standards",
                "value": Decimal("1000"),
                "priority": 20,
            },
            {
                "service_code": "pressing_article",
                "rule_type": "express_surcharge",
                "label": "Majoration express pressing",
                "value": Decimal("1000"),
                "priority": 30,
            },
        ]

        for data in pricing_rules:
            service = created_services[data["service_code"]]
            obj, created = PricingRule.objects.get_or_create(
                service=service,
                partner=None,
                rule_type=data["rule_type"],
                label=data["label"],
                defaults={
                    "value": data["value"],
                    "priority": data["priority"],
                    "is_active": True,
                },
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"Règle tarifaire créée : {obj.label}"))
            else:
                self.stdout.write(f"Règle tarifaire déjà existante : {obj.label}")

        self.stdout.write(self.style.SUCCESS("Seed FAGNI V2 terminé avec succès."))
