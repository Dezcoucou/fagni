from decimal import Decimal

from django.test import TestCase

from orders.models import (
    Customer,
    Order,
    OrderItem,
    ServiceCategory as LegacyServiceCategory,
    ServiceItem,
)
from services.models import (
    Service,
    ServiceCategory,
)
from services.resolution import (
    AmbiguousServiceResolutionError,
    ServiceCatalogResolutionError,
    ServiceResolutionError,
    resolve_v2_service_code_for_order,
    resolve_v2_service_for_order,
)


class OrderToV2ServiceCodeResolutionTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client Resolution",
            phone="0700009910",
        )

    def make_order(
        self,
        *,
        pricing_mode="item",
        bag_size=None,
    ):
        return Order.objects.create(
            customer=self.customer,
            pricing_mode=pricing_mode,
            bag_size=bag_size,
        )

    def make_legacy_service(
        self,
        *,
        category_slug,
        category_name,
        service_name,
        service_code,
    ):
        category, _ = LegacyServiceCategory.objects.get_or_create(
            slug=category_slug,
            defaults={
                "name": category_name,
            },
        )

        service, _ = ServiceItem.objects.get_or_create(
            category=category,
            code=service_code,
            defaults={
                "name": service_name,
                "default_price": Decimal("1000"),
                "is_active": True,
            },
        )

        return service

    def add_item(
        self,
        order,
        *,
        designation,
        service=None,
    ):
        return OrderItem.objects.create(
            order=order,
            service=service,
            designation=designation,
            quantity=1,
            unit_price=Decimal("1000"),
        )

    def test_bag_without_items_resolves_to_pressing_bag(self):
        order = self.make_order(
            pricing_mode="bag",
            bag_size="small",
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_bag",
        )

    def test_bag_with_pressing_item_still_resolves_to_pressing_bag(self):
        order = self.make_order(
            pricing_mode="bag",
            bag_size="medium",
        )
        self.add_item(
            order,
            designation="Chemise",
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_bag",
        )

    def test_bag_ignores_conflicting_legacy_items(self):
        order = self.make_order(
            pricing_mode="bag",
            bag_size="large",
        )

        self.add_item(
            order,
            designation="Chemise",
        )
        self.add_item(
            order,
            designation="Réparation talon",
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_bag",
        )

    def test_lavage_repassage_category_resolves_to_pressing_article(self):
        service = self.make_legacy_service(
            category_slug="lavage-repassage",
            category_name="Lavage & Repassage",
            service_name="Chemise",
            service_code="lavage-repassage-chemise",
        )
        order = self.make_order()
        self.add_item(
            order,
            designation="Chemise",
            service=service,
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_article",
        )

    def test_couettes_category_resolves_to_pressing_article(self):
        service = self.make_legacy_service(
            category_slug="couettes-couvertures",
            category_name="Couettes & couvertures",
            service_name="Couette 2 places",
            service_code="couette-2-places",
        )
        order = self.make_order()
        self.add_item(
            order,
            designation="Couette 2 places",
            service=service,
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_article",
        )

    def test_repassage_category_resolves_to_repassage(self):
        service = self.make_legacy_service(
            category_slug="repassage-seul",
            category_name="Repassage seul",
            service_name="Chemise repassage",
            service_code="repassage-chemise",
        )
        order = self.make_order()
        self.add_item(
            order,
            designation="Chemise (repassage)",
            service=service,
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "repassage",
        )

    def test_retouche_category_resolves_to_retouche_simple(self):
        service = self.make_legacy_service(
            category_slug="retouche",
            category_name="Retouche",
            service_name="Ourlet pantalon",
            service_code="retouche-ourlet",
        )
        order = self.make_order()
        self.add_item(
            order,
            designation="Ourlet pantalon",
            service=service,
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "retouche_simple",
        )

    def test_cordonnerie_category_resolves_to_cordonnerie_standard(self):
        service = self.make_legacy_service(
            category_slug="cordonnerie",
            category_name="Cordonnerie",
            service_name="Cirage",
            service_code="cordonnerie-cirage",
        )
        order = self.make_order()
        self.add_item(
            order,
            designation="Cirage",
            service=service,
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "cordonnerie_standard",
        )

    def test_unlinked_pantalon_is_reinferred_as_pressing(self):
        order = self.make_order()

        item = self.add_item(
            order,
            designation="Pantalon",
        )

        item.service_type = "cordonnerie"
        OrderItem.objects.filter(pk=item.pk).update(
            service_type="cordonnerie"
        )

        item.refresh_from_db()

        self.assertEqual(
            item.service_type,
            "cordonnerie",
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_article",
        )

    def test_unlinked_retouche_resolves_to_retouche_simple(self):
        order = self.make_order()
        self.add_item(
            order,
            designation="Ourlet pantalon",
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "retouche_simple",
        )

    def test_unlinked_cordonnerie_resolves_to_cordonnerie_standard(self):
        order = self.make_order()
        self.add_item(
            order,
            designation="Réparation talon",
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "cordonnerie_standard",
        )

    def test_unlinked_shoes_resolve_to_cordonnerie_standard(self):
        order = self.make_order()
        self.add_item(
            order,
            designation="Basket",
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "cordonnerie_standard",
        )

    def test_item_without_items_falls_back_to_pressing_article(self):
        order = self.make_order()

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_article",
        )

    def test_unknown_item_preserves_pressing_article_fallback(self):
        order = self.make_order()
        self.add_item(
            order,
            designation="Article inconnu",
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_article",
        )

    def test_conflicting_item_types_are_rejected(self):
        order = self.make_order()

        self.add_item(
            order,
            designation="Chemise",
        )
        self.add_item(
            order,
            designation="Ourlet pantalon",
        )

        with self.assertRaises(
            AmbiguousServiceResolutionError
        ):
            resolve_v2_service_code_for_order(order)

    def test_known_legacy_category_does_not_hide_unlinked_conflicting_item(self):
        pressing_service = self.make_legacy_service(
            category_slug="lavage-repassage",
            category_name="Lavage & Repassage",
            service_name="Chemise hybride",
            service_code="legacy-hybrid-pressing-chemise",
        )

        order = self.make_order()

        self.add_item(
            order,
            designation="Chemise",
            service=pressing_service,
        )

        self.add_item(
            order,
            designation="Ourlet pantalon",
            service=None,
        )

        with self.assertRaises(
            AmbiguousServiceResolutionError
        ):
            resolve_v2_service_code_for_order(order)

    def test_conflicting_legacy_categories_are_rejected(self):
        pressing_service = self.make_legacy_service(
            category_slug="lavage-repassage",
            category_name="Lavage & Repassage",
            service_name="Chemise",
            service_code="legacy-pressing-chemise",
        )

        retouche_service = self.make_legacy_service(
            category_slug="retouche",
            category_name="Retouche",
            service_name="Ourlet pantalon",
            service_code="legacy-retouche-ourlet",
        )

        order = self.make_order()

        self.add_item(
            order,
            designation="Chemise",
            service=pressing_service,
        )
        self.add_item(
            order,
            designation="Ourlet pantalon",
            service=retouche_service,
        )

        with self.assertRaises(
            AmbiguousServiceResolutionError
        ):
            resolve_v2_service_code_for_order(order)

    def test_order_service_type_is_not_required_for_resolution(self):
        order = self.make_order()
        self.add_item(
            order,
            designation="Chemise",
        )

        self.assertIsNone(order.service_type)

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_article",
        )

    def test_unknown_pricing_mode_is_rejected(self):
        order = self.make_order()

        Order.objects.filter(pk=order.pk).update(
            pricing_mode="legacy",
        )
        order.refresh_from_db()

        with self.assertRaises(ServiceResolutionError):
            resolve_v2_service_code_for_order(order)


class V2ServiceCatalogueResolutionTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            name="Client Catalogue Resolution",
            phone="0700009911",
        )

    def make_order(self):
        return Order.objects.create(
            customer=self.customer,
            pricing_mode="item",
        )

    def test_code_resolution_does_not_require_v2_catalogue(self):
        order = self.make_order()

        OrderItem.objects.create(
            order=order,
            designation="Chemise",
            quantity=1,
            unit_price=Decimal("1000"),
        )

        self.assertEqual(
            Service.objects.count(),
            0,
        )

        self.assertEqual(
            resolve_v2_service_code_for_order(order),
            "pressing_article",
        )

    def test_object_resolution_returns_active_service(self):
        category = ServiceCategory.objects.create(
            code="pressing",
            name="Pressing",
            is_active=True,
        )

        service = Service.objects.create(
            code="pressing_article",
            category=category,
            name="Pressing par article",
            is_active=True,
            pricing_mode="per_item",
        )

        order = self.make_order()

        OrderItem.objects.create(
            order=order,
            designation="Chemise",
            quantity=1,
            unit_price=Decimal("1000"),
        )

        resolved = resolve_v2_service_for_order(order)

        self.assertEqual(
            resolved.pk,
            service.pk,
        )

    def test_object_resolution_rejects_missing_catalogue_service(self):
        order = self.make_order()

        OrderItem.objects.create(
            order=order,
            designation="Chemise",
            quantity=1,
            unit_price=Decimal("1000"),
        )

        with self.assertRaises(
            ServiceCatalogResolutionError
        ):
            resolve_v2_service_for_order(order)

    def test_object_resolution_rejects_inactive_service(self):
        category = ServiceCategory.objects.create(
            code="pressing",
            name="Pressing",
            is_active=True,
        )

        Service.objects.create(
            code="pressing_article",
            category=category,
            name="Pressing par article",
            is_active=False,
            pricing_mode="per_item",
        )

        order = self.make_order()

        OrderItem.objects.create(
            order=order,
            designation="Chemise",
            quantity=1,
            unit_price=Decimal("1000"),
        )

        with self.assertRaises(
            ServiceCatalogResolutionError
        ):
            resolve_v2_service_for_order(order)
