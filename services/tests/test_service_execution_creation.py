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


class OrderCommercialFinalizationTests(TestCase):
    """
    Contrat canonique de finalisation commerciale FAGNI.

    Une commande ne doit devenir commercialement confirmée
    qu'après matérialisation réussie de ses ServiceExecution.
    """

    def setUp(self):
        from decimal import Decimal
        from orders.models import OrderItem

        self.Decimal = Decimal
        self.OrderItem = OrderItem

        self.category = ServiceCategory.objects.create(
            code="commercial-finalization-category",
            name="Commercial Finalization Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Commercial Finalization",
            phone="0700009950",
        )

    def create_service(
        self,
        *,
        code,
        name,
        is_active=True,
    ):
        return Service.objects.create(
            code=code,
            category=self.category,
            name=name,
            description="",
            is_active=is_active,
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

    def create_draft_order(self):
        return Order.objects.create(
            customer=self.customer,
            pricing_mode="item",
            is_draft=True,
            status="pending",
        )

    def add_item(
        self,
        order,
        *,
        designation,
        service_type,
    ):
        return self.OrderItem.objects.create(
            order=order,
            designation=designation,
            quantity=1,
            unit_price=self.Decimal("1000"),
            total=self.Decimal("1000"),
            service_type=service_type,
        )

    def test_finalization_materializes_services_before_unlocking_order(self):
        from services.services import finalize_commercial_order

        self.create_service(
            code="pressing_article",
            name="Pressing Article",
        )
        self.create_service(
            code="retouche_simple",
            name="Retouche",
        )

        order = self.create_draft_order()

        self.add_item(
            order,
            designation="Chemise",
            service_type="pressing",
        )
        self.add_item(
            order,
            designation="Pantalon retouche",
            service_type="retouche",
        )

        executions = finalize_commercial_order(order=order)

        order.refresh_from_db()

        self.assertFalse(order.is_draft)

        self.assertEqual(
            tuple(
                execution.service.code
                for execution in executions
            ),
            (
                "pressing_article",
                "retouche_simple",
            ),
        )

        self.assertEqual(
            order.service_executions.count(),
            2,
        )

    def test_finalization_is_idempotent(self):
        from services.services import finalize_commercial_order

        self.create_service(
            code="pressing_article",
            name="Pressing Article",
        )

        order = self.create_draft_order()

        self.add_item(
            order,
            designation="Chemise",
            service_type="pressing",
        )

        first = finalize_commercial_order(order=order)
        second = finalize_commercial_order(order=order)

        order.refresh_from_db()

        self.assertFalse(order.is_draft)

        self.assertEqual(
            tuple(execution.id for execution in first),
            tuple(execution.id for execution in second),
        )

        self.assertEqual(
            order.service_executions.count(),
            1,
        )

    def test_catalogue_failure_keeps_order_draft_and_creates_nothing(self):
        from services.resolution import ServiceCatalogResolutionError
        from services.services import finalize_commercial_order

        self.create_service(
            code="pressing_article",
            name="Pressing Article",
        )

        order = self.create_draft_order()

        self.add_item(
            order,
            designation="Chemise",
            service_type="pressing",
        )
        self.add_item(
            order,
            designation="Chaussure",
            service_type="cordonnerie",
        )

        with self.assertRaises(ServiceCatalogResolutionError):
            finalize_commercial_order(order=order)

        order.refresh_from_db()

        self.assertTrue(order.is_draft)
        self.assertEqual(
            order.service_executions.count(),
            0,
        )

    def test_finalization_reuses_existing_execution(self):
        from services.services import (
            create_service_execution,
            finalize_commercial_order,
        )

        pressing = self.create_service(
            code="pressing_article",
            name="Pressing Article",
        )
        self.create_service(
            code="retouche_simple",
            name="Retouche",
        )

        order = self.create_draft_order()

        self.add_item(
            order,
            designation="Chemise",
            service_type="pressing",
        )
        self.add_item(
            order,
            designation="Pantalon retouche",
            service_type="retouche",
        )

        existing = create_service_execution(
            order=order,
            service=pressing,
        )

        executions = finalize_commercial_order(order=order)

        self.assertEqual(
            executions[0].id,
            existing.id,
        )

        self.assertEqual(
            order.service_executions.count(),
            2,
        )

    def test_failed_finalization_never_exposes_confirmed_order(self):
        from services.resolution import ServiceCatalogResolutionError
        from services.services import finalize_commercial_order

        order = self.create_draft_order()

        self.add_item(
            order,
            designation="Article pressing",
            service_type="pressing",
        )

        with self.assertRaises(ServiceCatalogResolutionError):
            finalize_commercial_order(order=order)

        order.refresh_from_db()

        self.assertTrue(order.is_draft)


class OrderItemExecutionLinkMaterializationTests(TestCase):
    """
    Contrat de matérialisation du bridge pendant la création
    des ServiceExecution d'une commande.
    """

    def setUp(self):
        from decimal import Decimal

        from orders.models import OrderItem

        self.Decimal = Decimal
        self.OrderItem = OrderItem

        self.category = ServiceCategory.objects.create(
            code="link-materialization-category",
            name="Link Materialization Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Link Materialization",
            phone="0700009970",
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

    def test_each_item_is_linked_to_matching_execution(self):
        from services.services import (
            materialize_service_executions_for_order,
        )

        pressing_item = self.add_item("Chemise")
        retouche_item = self.add_item("Ourlet pantalon")
        cordonnerie_item = self.add_item("Réparation talon")

        executions = materialize_service_executions_for_order(
            order=self.order,
        )

        executions_by_code = {
            execution.service.code: execution
            for execution in executions
        }

        pressing_item.refresh_from_db()
        retouche_item.refresh_from_db()
        cordonnerie_item.refresh_from_db()

        self.assertEqual(
            pressing_item.service_execution_link.service_execution_id,
            executions_by_code["pressing_article"].id,
        )

        self.assertEqual(
            retouche_item.service_execution_link.service_execution_id,
            executions_by_code["retouche_simple"].id,
        )

        self.assertEqual(
            cordonnerie_item.service_execution_link.service_execution_id,
            executions_by_code["cordonnerie_standard"].id,
        )

    def test_multiple_same_family_items_share_same_execution(self):
        from services.services import (
            materialize_service_executions_for_order,
        )

        chemise = self.add_item("Chemise")
        pantalon = self.add_item("Pantalon")

        executions = materialize_service_executions_for_order(
            order=self.order,
        )

        self.assertEqual(len(executions), 1)

        chemise.refresh_from_db()
        pantalon.refresh_from_db()

        self.assertEqual(
            chemise.service_execution_link.service_execution_id,
            executions[0].id,
        )

        self.assertEqual(
            pantalon.service_execution_link.service_execution_id,
            executions[0].id,
        )

        self.assertEqual(
            executions[0].item_links.count(),
            2,
        )

    def test_link_materialization_is_idempotent(self):
        from services.models import ServiceExecutionItem
        from services.services import (
            materialize_service_executions_for_order,
        )

        self.add_item("Chemise")
        self.add_item("Ourlet pantalon")

        first = materialize_service_executions_for_order(
            order=self.order,
        )

        first_links = tuple(
            ServiceExecutionItem.objects
            .filter(service_execution__order=self.order)
            .order_by("order_item_id")
            .values_list(
                "id",
                "order_item_id",
                "service_execution_id",
            )
        )

        second = materialize_service_executions_for_order(
            order=self.order,
        )

        second_links = tuple(
            ServiceExecutionItem.objects
            .filter(service_execution__order=self.order)
            .order_by("order_item_id")
            .values_list(
                "id",
                "order_item_id",
                "service_execution_id",
            )
        )

        self.assertEqual(
            tuple(execution.id for execution in first),
            tuple(execution.id for execution in second),
        )

        self.assertEqual(first_links, second_links)
        self.assertEqual(len(second_links), 2)

    def test_existing_correct_link_is_reused(self):
        from services.models import ServiceExecutionItem
        from services.services import (
            create_service_execution,
            materialize_service_executions_for_order,
        )

        item = self.add_item("Chemise")

        execution = create_service_execution(
            order=self.order,
            service=self.services["pressing_article"],
        )

        existing_link = ServiceExecutionItem.objects.create(
            service_execution=execution,
            order_item=item,
        )

        executions = materialize_service_executions_for_order(
            order=self.order,
        )

        persisted_link = ServiceExecutionItem.objects.get(
            order_item=item,
        )

        self.assertEqual(executions[0].id, execution.id)
        self.assertEqual(persisted_link.id, existing_link.id)
        self.assertEqual(
            persisted_link.service_execution_id,
            execution.id,
        )

    def test_existing_wrong_link_is_rejected_not_moved(self):
        from services.models import ServiceExecutionItem
        from services.services import (
            create_service_execution,
            materialize_service_executions_for_order,
        )

        item = self.add_item("Chemise")

        wrong_execution = create_service_execution(
            order=self.order,
            service=self.services["retouche_simple"],
        )

        existing_link = ServiceExecutionItem.objects.create(
            service_execution=wrong_execution,
            order_item=item,
        )

        with self.assertRaises(ValueError):
            materialize_service_executions_for_order(
                order=self.order,
            )

        persisted_link = ServiceExecutionItem.objects.get(
            order_item=item,
        )

        self.assertEqual(
            persisted_link.id,
            existing_link.id,
        )

        self.assertEqual(
            persisted_link.service_execution_id,
            wrong_execution.id,
        )

    def test_bag_order_links_all_items_to_pressing_bag_execution(self):
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
            pricing_mode="bag",
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

        chemise = self.add_item("Chemise")
        ourlet = self.add_item("Ourlet pantalon")
        talon = self.add_item("Réparation talon")

        executions = materialize_service_executions_for_order(
            order=self.order,
        )

        self.assertEqual(len(executions), 1)
        self.assertEqual(
            executions[0].service_id,
            pressing_bag.id,
        )

        for item in (
            chemise,
            ourlet,
            talon,
        ):
            item.refresh_from_db()

            self.assertEqual(
                item.service_execution_link.service_execution_id,
                executions[0].id,
            )

        self.assertEqual(
            executions[0].item_links.count(),
            3,
        )
