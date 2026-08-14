from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
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
      jamais l'OrderItem commercial.
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
