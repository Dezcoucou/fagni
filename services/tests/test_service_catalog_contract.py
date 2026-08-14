from django.core.management import call_command
from django.test import TestCase

from services.models import Service


class ServiceCatalogContractTests(TestCase):
    def test_service_pricing_modes_include_bag(self):
        field = Service._meta.get_field("pricing_mode")

        available_values = {
            value
            for value, _label in field.choices
        }

        self.assertIn(
            "bag",
            available_values,
            "Le catalogue V2 doit représenter explicitement "
            "la tarification par sac.",
        )

    def test_seed_v2_materializes_pressing_bag_contract(self):
        call_command("seed_v2", verbosity=0)

        service = Service.objects.select_related("category").get(
            code="pressing_bag"
        )

        self.assertEqual(service.category.code, "pressing")
        self.assertEqual(
            service.primary_engine,
            Service.ENGINE_PICKUP_RETURN,
        )

        self.assertTrue(service.requires_partner)
        self.assertTrue(service.requires_logistics)

        self.assertFalse(service.requires_weighing)
        self.assertFalse(service.requires_appointment)
        self.assertFalse(service.requires_quote)
        self.assertFalse(service.requires_asset)
        self.assertFalse(service.requires_otp)
        self.assertFalse(service.requires_signature)

        self.assertEqual(service.pricing_mode, "bag")
        self.assertEqual(service.default_sla_hours, 48)

    def test_seed_v2_keeps_pressing_kilo_distinct_from_pressing_bag(self):
        call_command("seed_v2", verbosity=0)

        bag_service = Service.objects.get(code="pressing_bag")
        kilo_service = Service.objects.get(code="pressing_kilo")

        self.assertNotEqual(bag_service.pk, kilo_service.pk)

        self.assertEqual(bag_service.pricing_mode, "bag")
        self.assertFalse(bag_service.requires_weighing)

        self.assertEqual(kilo_service.pricing_mode, "per_kg")
        self.assertTrue(kilo_service.requires_weighing)

    def test_seed_v2_is_idempotent_for_pressing_bag(self):
        call_command("seed_v2", verbosity=0)

        first_id = Service.objects.get(
            code="pressing_bag"
        ).pk

        call_command("seed_v2", verbosity=0)

        self.assertEqual(
            Service.objects.filter(
                code="pressing_bag"
            ).count(),
            1,
        )

        self.assertEqual(
            Service.objects.get(
                code="pressing_bag"
            ).pk,
            first_id,
        )
