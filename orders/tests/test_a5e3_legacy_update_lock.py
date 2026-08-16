"""
FAGNI — LOT A5-E3

Contrat de sécurité de la route legacy orders:update.

Une commande commercialement matérialisée dans le moteur V2 ne doit plus
pouvoir être modifiée via l'ancienne vue générique orders.update.

Cette route mélange historiquement :
- statut ;
- partenaires ;
- notes ;
- lignes commerciales ;
- recalcul financier.

Après matérialisation, ces responsabilités doivent être portées par les
workflows spécialisés et non par cette vue legacy.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

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
    ServiceExecutionItem,
)
from services.services import create_service_execution


class LegacyUpdatePostMaterializationLockTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="a5e3-ops",
            password="test-password-a5e3",
        )

        self.customer = Customer.objects.create(
            name="Client A5-E3",
            phone="0700099301",
        )

        self.legacy_category = LegacyServiceCategory.objects.create(
            name="Pressing A5-E3",
            slug="pressing-a5e3",
        )

        self.legacy_service = ServiceItem.objects.create(
            category=self.legacy_category,
            name="Chemise A5-E3",
            default_price=Decimal("1000"),
        )

        self.order = Order.objects.create(
            customer=self.customer,
            pricing_mode="item",
            is_draft=False,
            payment_status="unpaid",
            status="pending",
            notes="Note originale A5-E3",
        )

        self.item = OrderItem.objects.create(
            order=self.order,
            service=self.legacy_service,
            designation="Chemise A5-E3",
            quantity=1,
            unit_price=Decimal("1000"),
        )

        self.v2_category = ServiceCategory.objects.create(
            code="a5e3-update-lock-category",
            name="A5-E3 Update Lock Category",
            is_active=True,
        )

        self.v2_service = Service.objects.create(
            code="a5e3-update-lock-service",
            category=self.v2_category,
            name="A5-E3 Update Lock Service",
            description="Service de test A5-E3.",
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

        self.execution = create_service_execution(
            order=self.order,
            service=self.v2_service,
        )

        ServiceExecutionItem.objects.create(
            service_execution=self.execution,
            order_item=self.item,
        )

        self.client = Client()
        self.client.force_login(self.user)

    def test_update_route_requires_authentication(self):
        anonymous = Client()

        response = anonymous.get(
            reverse("orders:update", args=[self.order.id]),
        )

        self.assertEqual(
            response.status_code,
            302,
            "La route back-office orders:update doit exiger une authentification.",
        )

        self.assertIn(
            "/accounts/login/",
            response.url,
        )

    def test_materialized_order_get_cannot_open_legacy_editor(self):
        response = self.client.get(
            reverse("orders:update", args=[self.order.id]),
        )

        self.assertEqual(
            response.status_code,
            302,
            "Une commande matérialisée ne doit plus ouvrir l'éditeur legacy.",
        )

        self.assertEqual(
            response.url,
            reverse("orders:detail", args=[self.order.id]),
        )

    def test_materialized_order_post_cannot_change_item(self):
        original = {
            "designation": self.item.designation,
            "quantity": self.item.quantity,
            "unit_price": self.item.unit_price,
            "service_id": self.item.service_id,
        }

        response = self.client.post(
            reverse("orders:update", args=[self.order.id]),
            data={
                "status": self.order.status,
                "order_notes": "Tentative mutation A5-E3",
                "item_index[]": ["0"],
                "item_id[]": [str(self.item.id)],
                "service_id[]": [str(self.legacy_service.id)],
                "designation[]": ["Chemise trafiquée"],
                "quantity[]": ["9"],
                "unit_price[]": ["99999"],
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response.url,
            reverse("orders:detail", args=[self.order.id]),
        )

        self.item.refresh_from_db()

        self.assertEqual(
            self.item.designation,
            original["designation"],
        )
        self.assertEqual(
            self.item.quantity,
            original["quantity"],
        )
        self.assertEqual(
            self.item.unit_price,
            original["unit_price"],
        )
        self.assertEqual(
            self.item.service_id,
            original["service_id"],
        )

    def test_materialized_order_post_cannot_change_order_metadata(self):
        original_status = self.order.status
        original_notes = self.order.notes

        response = self.client.post(
            reverse("orders:update", args=[self.order.id]),
            data={
                "status": "in_progress",
                "order_notes": "Note modifiée par route legacy",
                "item_index[]": ["0"],
                "item_id[]": [str(self.item.id)],
                "service_id[]": [str(self.legacy_service.id)],
                "designation[]": [self.item.designation],
                "quantity[]": [str(self.item.quantity)],
                "unit_price[]": [str(self.item.unit_price)],
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.status,
            original_status,
            "La route legacy entière doit être gelée après matérialisation.",
        )

        self.assertEqual(
            self.order.notes,
            original_notes,
            "Les métadonnées ne doivent pas être modifiées via cette route "
            "une fois la commande matérialisée.",
        )

    def test_unmaterialized_unpaid_order_remains_editable(self):
        draft_order = Order.objects.create(
            customer=self.customer,
            pricing_mode="item",
            is_draft=True,
            payment_status="unpaid",
            status="pending",
            notes="Avant modification",
        )

        draft_item = OrderItem.objects.create(
            order=draft_order,
            service=self.legacy_service,
            designation="Chemise brouillon",
            quantity=1,
            unit_price=Decimal("1000"),
        )

        response = self.client.post(
            reverse("orders:update", args=[draft_order.id]),
            data={
                "status": "pending",
                "order_notes": "Après modification",
                "item_index[]": ["0"],
                "item_id[]": [str(draft_item.id)],
                "service_id[]": [str(self.legacy_service.id)],
                "designation[]": ["Chemise brouillon modifiée"],
                "quantity[]": ["2"],
                "unit_price[]": ["1000"],
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        draft_item.refresh_from_db()
        draft_order.refresh_from_db()

        self.assertEqual(
            draft_item.designation,
            "Chemise brouillon modifiée",
        )
        self.assertEqual(
            draft_item.quantity,
            2,
        )
        self.assertEqual(
            draft_order.notes,
            "Après modification",
        )
