from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from logistics.orchestrator import (
    AmbiguousServiceExecutionError,
)
from orders.models import Customer, Order
from services.models import Service, ServiceCategory
from services.services import create_service_execution


class RunV2FlowCommandTests(TestCase):
    """
    Contrat de frontière CLI pour run_v2_flow.

    Garanties :
    - une ServiceExecution explicite peut être sélectionnée ;
    - une ServiceExecution inexistante est refusée ;
    - une ServiceExecution appartenant à une autre Order est refusée ;
    - l'absence de sélection explicite reste autorisée ;
    - les ambiguïtés détectées par l'orchestrateur deviennent
      des CommandError propres côté CLI.
    """

    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="run-v2-flow-cli-category",
            name="Run V2 Flow CLI Category",
            is_active=True,
        )

        self.service = Service.objects.create(
            code="run-v2-flow-cli-service",
            category=self.category,
            name="Run V2 Flow CLI Service",
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

        self.customer = Customer.objects.create(
            name="Client Run V2 Flow CLI",
            phone="0700007010",
        )

        self.order_a = Order.objects.create(
            customer=self.customer,
            pickup_address="Cocody",
            delivery_address="Riviera",
        )

        self.order_b = Order.objects.create(
            customer=self.customer,
            pickup_address="Cocody",
            delivery_address="Riviera",
        )

    def build_result(self, *, order, service_execution):
        return {
            "order": order,
            "service_execution": service_execution,
            "mission": SimpleNamespace(
                code="MSN-CLI-001",
                status="completed",
            ),
            "partner_job": SimpleNamespace(
                code="JOB-CLI-001",
                status="ready",
            ),
            "weighing_record": SimpleNamespace(
                net_weight="3.50",
                unit="kg",
                weighing_stage="partner_reception",
            ),
            "quote": SimpleNamespace(
                id=999,
                total_amount="1000.00",
                currency="XOF",
            ),
            "incident": None,
        }

    def test_explicit_service_execution_is_passed_to_orchestrator(self):
        execution = create_service_execution(
            order=self.order_a,
            service=self.service,
        )

        stdout = StringIO()

        with patch(
            "logistics.management.commands.run_v2_flow."
            "run_minimal_v2_flow"
        ) as mocked_run:
            mocked_run.return_value = self.build_result(
                order=self.order_a,
                service_execution=execution,
            )

            call_command(
                "run_v2_flow",
                "--order-id",
                str(self.order_a.id),
                "--service-execution-id",
                str(execution.id),
                stdout=stdout,
            )

        mocked_run.assert_called_once()

        kwargs = mocked_run.call_args.kwargs

        self.assertEqual(
            kwargs["order"].id,
            self.order_a.id,
        )

        self.assertEqual(
            kwargs["service_execution"].id,
            execution.id,
        )

        self.assertFalse(
            kwargs["create_incident_flag"]
        )

        self.assertIn(
            f"ServiceExecution résolue : #{execution.id}",
            stdout.getvalue(),
        )

    def test_missing_service_execution_id_is_rejected(self):
        with patch(
            "logistics.management.commands.run_v2_flow."
            "run_minimal_v2_flow"
        ) as mocked_run:
            with self.assertRaises(CommandError):
                call_command(
                    "run_v2_flow",
                    "--order-id",
                    str(self.order_a.id),
                    "--service-execution-id",
                    "999999999",
                )

        mocked_run.assert_not_called()

    def test_execution_from_another_order_is_rejected(self):
        execution = create_service_execution(
            order=self.order_b,
            service=self.service,
        )

        with patch(
            "logistics.management.commands.run_v2_flow."
            "run_minimal_v2_flow"
        ) as mocked_run:
            with self.assertRaises(CommandError) as context:
                call_command(
                    "run_v2_flow",
                    "--order-id",
                    str(self.order_a.id),
                    "--service-execution-id",
                    str(execution.id),
                )

        mocked_run.assert_not_called()

        self.assertIn(
            "ServiceExecution incompatible",
            str(context.exception),
        )

    def test_no_explicit_execution_preserves_orchestrator_resolution(self):
        stdout = StringIO()

        with patch(
            "logistics.management.commands.run_v2_flow."
            "run_minimal_v2_flow"
        ) as mocked_run:
            mocked_run.return_value = self.build_result(
                order=self.order_a,
                service_execution=None,
            )

            call_command(
                "run_v2_flow",
                "--order-id",
                str(self.order_a.id),
                stdout=stdout,
            )

        kwargs = mocked_run.call_args.kwargs

        self.assertIsNone(
            kwargs["service_execution"]
        )

        self.assertIn(
            "compatibilité legacy",
            stdout.getvalue(),
        )

    def test_orchestrator_ambiguity_becomes_command_error(self):
        with patch(
            "logistics.management.commands.run_v2_flow."
            "run_minimal_v2_flow"
        ) as mocked_run:
            mocked_run.side_effect = (
                AmbiguousServiceExecutionError(
                    "Commande multiservice ambiguë."
                )
            )

            with self.assertRaises(CommandError) as context:
                call_command(
                    "run_v2_flow",
                    "--order-id",
                    str(self.order_a.id),
                )

        self.assertIn(
            "Commande multiservice ambiguë",
            str(context.exception),
        )

    def test_create_incident_flag_is_propagated(self):
        execution = create_service_execution(
            order=self.order_a,
            service=self.service,
        )

        with patch(
            "logistics.management.commands.run_v2_flow."
            "run_minimal_v2_flow"
        ) as mocked_run:
            mocked_run.return_value = self.build_result(
                order=self.order_a,
                service_execution=execution,
            )

            call_command(
                "run_v2_flow",
                "--order-id",
                str(self.order_a.id),
                "--service-execution-id",
                str(execution.id),
                "--create-incident",
            )

        kwargs = mocked_run.call_args.kwargs

        self.assertTrue(
            kwargs["create_incident_flag"]
        )
