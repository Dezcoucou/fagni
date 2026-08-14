from django.core.exceptions import ValidationError
from django.test import TestCase

from orders.models import Customer, Order
from services.models import (
    CustomerAsset,
    Service,
    ServiceCategory,
    ServiceExecution,
)
from services.services import create_service_execution


class ServiceExecutionCreationTests(TestCase):
    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="creation-contract",
            name="Creation Contract",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="creation-service",
            category=self.category,
            name="Creation Service",
            description="Service utilisé pour tester la factory canonique.",
            is_active=True,
            primary_engine=Service.ENGINE_ONSITE,
            requires_partner=True,
            requires_logistics=False,
            requires_weighing=True,
            requires_appointment=True,
            requires_quote=True,
            requires_asset=True,
            requires_otp=False,
            requires_signature=True,
            pricing_mode="fixed",
            default_sla_hours=36,
        )

        self.customer = Customer.objects.create(
            name="Client Creation",
            phone="0700009901",
        )

        self.order = Order.objects.create(
            customer=self.customer,
        )

        self.asset = CustomerAsset.objects.create(
            customer=self.customer,
            asset_type=CustomerAsset.ASSET_TYPE_VEHICLE,
            name="Véhicule création",
            reference="CI-CREATE-001",
        )

    def test_creation_uses_pending_status_and_service_engine(self):
        execution = create_service_execution(
            order=self.order,
            service=self.service,
            asset=self.asset,
        )

        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_PENDING,
        )
        self.assertEqual(
            execution.execution_engine,
            self.service.primary_engine,
        )

    def test_creation_allocates_sequence_per_order(self):
        execution_1 = create_service_execution(
            order=self.order,
            service=self.service,
            asset=self.asset,
        )

        execution_2 = create_service_execution(
            order=self.order,
            service=self.service,
            asset=self.asset,
        )

        self.assertEqual(execution_1.sequence_index, 1)
        self.assertEqual(execution_2.sequence_index, 2)

    def test_sequence_restarts_for_another_order(self):
        other_order = Order.objects.create(
            customer=self.customer,
        )

        first = create_service_execution(
            order=self.order,
            service=self.service,
            asset=self.asset,
        )

        other_first = create_service_execution(
            order=other_order,
            service=self.service,
            asset=self.asset,
        )

        self.assertEqual(first.sequence_index, 1)
        self.assertEqual(other_first.sequence_index, 1)

    def test_creation_snapshots_service_configuration(self):
        execution = create_service_execution(
            order=self.order,
            service=self.service,
            asset=self.asset,
        )

        snapshot = execution.service_snapshot_json

        self.assertEqual(snapshot["service_id"], self.service.id)
        self.assertEqual(snapshot["code"], self.service.code)
        self.assertEqual(snapshot["name"], self.service.name)
        self.assertEqual(
            snapshot["primary_engine"],
            Service.ENGINE_ONSITE,
        )
        self.assertEqual(snapshot["pricing_mode"], "fixed")
        self.assertEqual(snapshot["default_sla_hours"], 36)

        self.assertEqual(
            snapshot["category"]["id"],
            self.category.id,
        )
        self.assertEqual(
            snapshot["category"]["code"],
            self.category.code,
        )

        self.assertTrue(
            snapshot["requirements"]["requires_partner"]
        )
        self.assertFalse(
            snapshot["requirements"]["requires_logistics"]
        )
        self.assertTrue(
            snapshot["requirements"]["requires_asset"]
        )
        self.assertTrue(
            snapshot["requirements"]["requires_signature"]
        )

    def test_snapshot_and_engine_do_not_change_when_service_changes(self):
        execution = create_service_execution(
            order=self.order,
            service=self.service,
            asset=self.asset,
        )

        self.service.primary_engine = Service.ENGINE_APPOINTMENT
        self.service.default_sla_hours = 72
        self.service.requires_signature = False
        self.service.save(
            update_fields=[
                "primary_engine",
                "default_sla_hours",
                "requires_signature",
            ]
        )

        execution.refresh_from_db()

        self.assertEqual(
            execution.execution_engine,
            Service.ENGINE_ONSITE,
        )
        self.assertEqual(
            execution.service_snapshot_json["primary_engine"],
            Service.ENGINE_ONSITE,
        )
        self.assertEqual(
            execution.service_snapshot_json["default_sla_hours"],
            36,
        )
        self.assertTrue(
            execution.service_snapshot_json[
                "requirements"
            ]["requires_signature"]
        )

    def test_metadata_is_copied_into_execution(self):
        metadata = {
            "source": "creation-contract-test",
            "external_reference": "EXT-001",
        }

        execution = create_service_execution(
            order=self.order,
            service=self.service,
            asset=self.asset,
            metadata_json=metadata,
            notes="Création canonique test",
        )

        self.assertEqual(execution.metadata_json, metadata)
        self.assertEqual(
            execution.notes,
            "Création canonique test",
        )

    def test_asset_from_another_customer_is_rejected(self):
        other_customer = Customer.objects.create(
            name="Autre client",
            phone="0700009902",
        )

        foreign_asset = CustomerAsset.objects.create(
            customer=other_customer,
            asset_type=CustomerAsset.ASSET_TYPE_VEHICLE,
            name="Véhicule étranger",
        )

        with self.assertRaises(ValidationError):
            create_service_execution(
                order=self.order,
                service=self.service,
                asset=foreign_asset,
            )

    def test_creation_without_asset_remains_allowed_at_model_level(self):
        execution = create_service_execution(
            order=self.order,
            service=self.service,
        )

        self.assertIsNone(execution.asset_id)
        self.assertEqual(
            execution.status,
            ServiceExecution.STATUS_PENDING,
        )


class OrderMultiserviceExecutionMaterializationTests(TestCase):
    def setUp(self):
        from decimal import Decimal
        from orders.models import OrderItem

        self.Decimal = Decimal
        self.OrderItem = OrderItem

        self.category = ServiceCategory.objects.create(
            code="materialization-category",
            name="Materialization Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Materialization",
            phone="0700009940",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            pricing_mode="item",
        )

        self.services = {}

        for code, name in (
            ("pressing_article", "Pressing article"),
            ("retouche_simple", "Retouche simple"),
            ("cordonnerie_standard", "Cordonnerie standard"),
        ):
            self.services[code] = Service.objects.create(
                code=code,
                category=self.category,
                name=name,
                description="",
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

    def add_item(self, designation):
        return self.OrderItem.objects.create(
            order=self.order,
            designation=designation,
            quantity=1,
            unit_price=self.Decimal("1000"),
        )

    def test_materializes_one_execution_per_resolved_service(self):
        from services.services import (
            materialize_service_executions_for_order,
        )

        self.add_item("Chemise")
        self.add_item("Ourlet pantalon")
        self.add_item("Réparation talon")

        executions = materialize_service_executions_for_order(
            order=self.order,
        )

        self.assertEqual(
            tuple(execution.service.code for execution in executions),
            (
                "pressing_article",
                "retouche_simple",
                "cordonnerie_standard",
            ),
        )

        self.assertEqual(
            tuple(execution.sequence_index for execution in executions),
            (1, 2, 3),
        )

    def test_materialization_is_idempotent(self):
        from services.services import (
            materialize_service_executions_for_order,
        )

        self.add_item("Chemise")
        self.add_item("Ourlet pantalon")

        first = materialize_service_executions_for_order(
            order=self.order,
        )

        second = materialize_service_executions_for_order(
            order=self.order,
        )

        self.assertEqual(
            tuple(execution.id for execution in first),
            tuple(execution.id for execution in second),
        )

        self.assertEqual(
            self.order.service_executions.count(),
            2,
        )

    def test_existing_execution_is_reused_and_missing_one_created(self):
        from services.services import (
            create_service_execution,
            materialize_service_executions_for_order,
        )

        self.add_item("Chemise")
        self.add_item("Ourlet pantalon")

        existing = create_service_execution(
            order=self.order,
            service=self.services["pressing_article"],
        )

        executions = materialize_service_executions_for_order(
            order=self.order,
        )

        self.assertEqual(executions[0].id, existing.id)

        self.assertEqual(
            tuple(execution.service.code for execution in executions),
            (
                "pressing_article",
                "retouche_simple",
            ),
        )

        self.assertEqual(
            self.order.service_executions.count(),
            2,
        )

    def test_single_family_creates_single_execution(self):
        from services.services import (
            materialize_service_executions_for_order,
        )

        self.add_item("Chemise")
        self.add_item("Pantalon")

        executions = materialize_service_executions_for_order(
            order=self.order,
        )

        self.assertEqual(len(executions), 1)

        self.assertEqual(
            executions[0].service.code,
            "pressing_article",
        )

    def test_bag_order_materializes_only_pressing_bag(self):
        from services.services import (
            materialize_service_executions_for_order,
        )

        pressing_bag = Service.objects.create(
            code="pressing_bag",
            category=self.category,
            name="Pressing bag",
            description="",
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

        self.order.pricing_mode = "bag"
        self.order.bag_size = "small"
        self.order.save(
            update_fields=[
                "pricing_mode",
                "bag_size",
            ]
        )

        self.add_item("Chemise")
        self.add_item("Ourlet pantalon")

        executions = materialize_service_executions_for_order(
            order=self.order,
        )

        self.assertEqual(len(executions), 1)
        self.assertEqual(
            executions[0].service_id,
            pressing_bag.id,
        )

    def test_missing_catalogue_service_creates_nothing(self):
        from services.resolution import ServiceCatalogResolutionError
        from services.services import (
            materialize_service_executions_for_order,
        )

        self.add_item("Chemise")
        self.add_item("Ourlet pantalon")

        self.services["retouche_simple"].delete()

        with self.assertRaises(ServiceCatalogResolutionError):
            materialize_service_executions_for_order(
                order=self.order,
            )

        self.assertEqual(
            self.order.service_executions.count(),
            0,
        )
