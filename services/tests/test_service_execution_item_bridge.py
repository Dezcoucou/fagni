from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import RestrictedError
from django.test import TestCase

from orders.models import Customer, Order, OrderItem
from services.models import (
    Service,
    ServiceCategory,
    ServiceExecutionItem,
)
from services.services import create_service_execution


class ServiceExecutionItemBridgeTests(TestCase):
    """
    Contrat structurel du bridge canonique :

    Order
      ├── OrderItem
      └── ServiceExecution
              └── ServiceExecutionItem
                      └── OrderItem

    Garanties :
    - plusieurs OrderItem peuvent appartenir à une même ServiceExecution ;
    - un OrderItem ne peut appartenir qu'à une seule ServiceExecution ;
    - OrderItem et ServiceExecution doivent appartenir à la même Order ;
    - supprimer une ServiceExecution supprime uniquement le bridge,
      jamais l'OrderItem commercial ;
    - une fois un OrderItem matérialisé dans une ServiceExecution,
      son contrat commercial devient immuable ;
    - un OrderItem matérialisé ne peut plus être supprimé isolément ;
    - une suppression cohérente de toute la commande doit rester possible.
    """

    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="bridge-contract-category",
            name="Bridge Contract Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="bridge-contract-service",
            category=self.category,
            name="Bridge Contract Service",
            description="Service utilisé pour tester le bridge canonique.",
            is_active=True,
            primary_engine=Service.ENGINE_PICKUP_RETURN,
            requires_partner=False,
            requires_logistics=False,
            requires_weighing=False,
            requires_appointment=False,
            requires_quote=False,
            requires_asset=False,
            requires_otp=False,
            requires_signature=False,
            pricing_mode="fixed",
            default_sla_hours=24,
        )

        self.customer = Customer.objects.create(
            name="Client Bridge",
            phone="0700009960",
        )

        self.order_a = Order.objects.create(
            customer=self.customer,
            pricing_mode="item",
        )

        self.order_b = Order.objects.create(
            customer=self.customer,
            pricing_mode="item",
        )

        self.item_a_1 = OrderItem.objects.create(
            order=self.order_a,
            designation="Chemise",
            quantity=1,
            unit_price=1000,
        )

        self.item_a_2 = OrderItem.objects.create(
            order=self.order_a,
            designation="Pantalon",
            quantity=1,
            unit_price=1500,
        )

        self.item_b = OrderItem.objects.create(
            order=self.order_b,
            designation="Veste",
            quantity=1,
            unit_price=2000,
        )

        self.execution_a_1 = create_service_execution(
            order=self.order_a,
            service=self.service,
        )

        self.execution_a_2 = create_service_execution(
            order=self.order_a,
            service=self.service,
        )

        self.execution_b = create_service_execution(
            order=self.order_b,
            service=self.service,
        )

    def _materialize_item_a_1(self):
        return ServiceExecutionItem.objects.create(
            service_execution=self.execution_a_1,
            order_item=self.item_a_1,
        )

    def test_same_order_link_is_allowed(self):
        link = ServiceExecutionItem.objects.create(
            service_execution=self.execution_a_1,
            order_item=self.item_a_1,
        )

        self.assertEqual(
            link.service_execution_id,
            self.execution_a_1.id,
        )

        self.assertEqual(
            link.order_item_id,
            self.item_a_1.id,
        )

    def test_multiple_items_can_belong_to_same_execution(self):
        ServiceExecutionItem.objects.create(
            service_execution=self.execution_a_1,
            order_item=self.item_a_1,
        )

        ServiceExecutionItem.objects.create(
            service_execution=self.execution_a_1,
            order_item=self.item_a_2,
        )

        self.assertEqual(
            self.execution_a_1.item_links.count(),
            2,
        )

        self.assertEqual(
            {
                link.order_item_id
                for link in self.execution_a_1.item_links.all()
            },
            {
                self.item_a_1.id,
                self.item_a_2.id,
            },
        )

    def test_cross_order_link_is_rejected(self):
        with self.assertRaises(ValidationError):
            ServiceExecutionItem.objects.create(
                service_execution=self.execution_b,
                order_item=self.item_a_1,
            )

        self.assertFalse(
            ServiceExecutionItem.objects.filter(
                order_item=self.item_a_1,
            ).exists()
        )

    def test_order_item_cannot_be_linked_to_two_executions(self):
        ServiceExecutionItem.objects.create(
            service_execution=self.execution_a_1,
            order_item=self.item_a_1,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ServiceExecutionItem.objects.create(
                    service_execution=self.execution_a_2,
                    order_item=self.item_a_1,
                )

        self.assertEqual(
            ServiceExecutionItem.objects.filter(
                order_item=self.item_a_1,
            ).count(),
            1,
        )

        persisted_link = ServiceExecutionItem.objects.get(
            order_item=self.item_a_1,
        )

        self.assertEqual(
            persisted_link.service_execution_id,
            self.execution_a_1.id,
        )

    def test_deleting_execution_deletes_bridge_but_preserves_order_item(self):
        item_id = self.item_a_1.id

        ServiceExecutionItem.objects.create(
            service_execution=self.execution_a_1,
            order_item=self.item_a_1,
        )

        self.assertTrue(
            ServiceExecutionItem.objects.filter(
                order_item_id=item_id,
            ).exists()
        )

        self.execution_a_1.delete()

        self.assertFalse(
            ServiceExecutionItem.objects.filter(
                order_item_id=item_id,
            ).exists()
        )

        self.assertTrue(
            OrderItem.objects.filter(
                pk=item_id,
            ).exists()
        )

    def test_reverse_relations_expose_the_contract(self):
        link = ServiceExecutionItem.objects.create(
            service_execution=self.execution_a_1,
            order_item=self.item_a_1,
        )

        self.assertEqual(
            self.item_a_1.service_execution_link.id,
            link.id,
        )

        self.assertEqual(
            self.execution_a_1.item_links.get().id,
            link.id,
        )

    # ==========================================================
    # A5-E3 — intégrité post-matérialisation
    # ==========================================================

    def test_materialized_item_rejects_quantity_change(self):
        self._materialize_item_a_1()

        self.item_a_1.quantity = 9

        with self.assertRaises(ValidationError):
            self.item_a_1.save()

        self.item_a_1.refresh_from_db()

        self.assertEqual(
            self.item_a_1.quantity,
            1,
        )

    def test_materialized_item_rejects_unit_price_change(self):
        self._materialize_item_a_1()

        self.item_a_1.unit_price = 99999

        with self.assertRaises(ValidationError):
            self.item_a_1.save()

        self.item_a_1.refresh_from_db()

        self.assertEqual(
            self.item_a_1.unit_price,
            1000,
        )

    def test_materialized_item_rejects_designation_change(self):
        self._materialize_item_a_1()

        self.item_a_1.designation = "Designation alteree"

        with self.assertRaises(ValidationError):
            self.item_a_1.save()

        self.item_a_1.refresh_from_db()

        self.assertEqual(
            self.item_a_1.designation,
            "Chemise",
        )

    def test_materialized_item_rejects_service_type_change(self):
        self._materialize_item_a_1()

        original_service_type = self.item_a_1.service_type

        self.item_a_1.service_type = "cordonnerie"

        with self.assertRaises(ValidationError):
            self.item_a_1.save()

        self.item_a_1.refresh_from_db()

        self.assertEqual(
            self.item_a_1.service_type,
            original_service_type,
        )

    def test_materialized_item_rejects_service_change(self):
        self._materialize_item_a_1()

        from orders.models import ServiceCategory as LegacyServiceCategory
        from orders.models import ServiceItem

        legacy_category = LegacyServiceCategory.objects.create(
            name="Autre categorie legacy",
            slug="autre-categorie-a5e3",
        )

        other_service = ServiceItem.objects.create(
            category=legacy_category,
            name="Autre service legacy",
            default_price=2500,
        )

        original_service_id = self.item_a_1.service_id

        self.item_a_1.service = other_service

        with self.assertRaises(ValidationError):
            self.item_a_1.save()

        self.item_a_1.refresh_from_db()

        self.assertEqual(
            self.item_a_1.service_id,
            original_service_id,
        )

    def test_materialized_item_rejects_order_change(self):
        self._materialize_item_a_1()

        original_order_id = self.item_a_1.order_id

        self.item_a_1.order = self.order_b

        with self.assertRaises(ValidationError):
            self.item_a_1.save()

        self.item_a_1.refresh_from_db()

        self.assertEqual(
            self.item_a_1.order_id,
            original_order_id,
        )

    def test_materialized_item_allows_save_without_commercial_change(self):
        self._materialize_item_a_1()

        self.item_a_1.refresh_from_db()

        try:
            self.item_a_1.save()
        except ValidationError as exc:
            self.fail(
                "Un save sans modification commerciale ne doit pas être "
                f"refusé : {exc}"
            )

        self.assertTrue(
            ServiceExecutionItem.objects.filter(
                order_item=self.item_a_1,
            ).exists()
        )

    def test_materialized_item_direct_delete_is_restricted(self):
        self._materialize_item_a_1()

        item_id = self.item_a_1.id
        execution_id = self.execution_a_1.id

        with self.assertRaises(RestrictedError):
            self.item_a_1.delete()

        self.assertTrue(
            OrderItem.objects.filter(
                pk=item_id,
            ).exists()
        )

        self.assertTrue(
            ServiceExecutionItem.objects.filter(
                order_item_id=item_id,
                service_execution_id=execution_id,
            ).exists()
        )

    def test_materialized_item_queryset_delete_is_restricted(self):
        self._materialize_item_a_1()

        item_id = self.item_a_1.id
        execution_id = self.execution_a_1.id

        with self.assertRaises(RestrictedError):
            OrderItem.objects.filter(
                pk=item_id,
            ).delete()

        self.assertTrue(
            OrderItem.objects.filter(
                pk=item_id,
            ).exists()
        )

        self.assertTrue(
            ServiceExecutionItem.objects.filter(
                order_item_id=item_id,
                service_execution_id=execution_id,
            ).exists()
        )

    def test_deleting_whole_order_remains_allowed_after_materialization(self):
        self._materialize_item_a_1()

        order_id = self.order_a.id
        item_id = self.item_a_1.id
        execution_id = self.execution_a_1.id

        self.order_a.delete()

        self.assertFalse(
            Order.objects.filter(
                pk=order_id,
            ).exists()
        )

        self.assertFalse(
            OrderItem.objects.filter(
                pk=item_id,
            ).exists()
        )

        self.assertFalse(
            ServiceExecutionItem.objects.filter(
                service_execution_id=execution_id,
            ).exists()
        )
