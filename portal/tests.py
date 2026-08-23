from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from orders.models import (
    Order,
    ServiceCategory,
    ServiceItem,
)
from services.models import Service, ServiceExecution, ServiceExecutionItem


class PortalCreateOrderV2Tests(TestCase):
    """
    Contrat Portal → Order draft → finalisation commerciale V2.

    Le catalogue legacy orders.ServiceItem résout une famille métier
    canonique, puis services.Service fournit le moteur V2 correspondant.
    """

    def setUp(self):
        # ---------------------------------------------------------
        # CATALOGUE LEGACY / COMMERCIAL
        # ---------------------------------------------------------
        self.pressing_category, _ = ServiceCategory.objects.get_or_create(
            slug="pressing",
            defaults={
                "name": "Pressing",
            },
        )

        self.retouche_category, _ = ServiceCategory.objects.get_or_create(
            slug="retouche",
            defaults={
                "name": "Retouche",
            },
        )

        self.pressing_item, _ = ServiceItem.objects.get_or_create(
            category=self.pressing_category,
            code="chemise",
            defaults={
                "name": "Chemise",
                "default_price": Decimal("500"),
            },
        )

        self.retouche_item, _ = ServiceItem.objects.get_or_create(
            category=self.retouche_category,
            code="ourlet",
            defaults={
                "name": "Ourlet",
                "default_price": Decimal("1000"),
            },
        )

        # ---------------------------------------------------------
        # CATALOGUE V2 / SERVICES
        # ---------------------------------------------------------
        self.pressing_service, _ = Service.objects.get_or_create(
            code="pressing_article",
            defaults={
                "name": "Pressing Article",
                "pricing_mode": "fixed",
                "default_sla_hours": 48,
                "primary_engine": Service.ENGINE_PICKUP_RETURN,
                "is_active": True,
            },
        )

        self.retouche_service, _ = Service.objects.get_or_create(
            code="retouche_simple",
            defaults={
                "name": "Retouche Simple",
                "pricing_mode": "fixed",
                "default_sla_hours": 24,
                "primary_engine": Service.ENGINE_PICKUP_RETURN,
                "is_active": True,
            },
        )

        # Garantir que les objets utilisés par le test sont actifs.
        self.pressing_service.is_active = True
        self.pressing_service.save(
            update_fields=["is_active"]
        )

        self.retouche_service.is_active = True
        self.retouche_service.save(
            update_fields=["is_active"]
        )

    def _post_order(
        self,
        *,
        phone,
        name,
        service_items,
        designations,
        quantities,
        unit_prices,
    ):
        return self.client.post(
            reverse("portal:public_create_order"),
            data={
                "client_phone": phone,
                "client_name": name,
                "client_address": "Riviera 3",
                "service_id[]": [
                    str(item.id)
                    for item in service_items
                ],
                "designation[]": designations,
                "quantity[]": quantities,
                "unit_price[]": unit_prices,
            },
        )

    def test_nominal_creates_order_with_service_execution(self):
        """
        Parcours nominal :
        Order créée en draft → ServiceExecution matérialisée
        → Order finalisée.
        """
        response = self._post_order(
            phone="0700000001",
            name="Client Test",
            service_items=[self.pressing_item],
            designations=["Chemise"],
            quantities=["2"],
            unit_prices=["500"],
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("merci", response.url)

        order = Order.objects.get(
            customer__phone="0700000001"
        )

        self.assertFalse(order.is_draft)
        self.assertEqual(order.status, "pending")

        self.assertEqual(order.items.count(), 1)

        item = order.items.first()

        self.assertEqual(item.designation, "Chemise")
        self.assertEqual(item.quantity, 2)

        executions = ServiceExecution.objects.filter(
            order=order
        )

        self.assertEqual(executions.count(), 1)

        execution = executions.first()

        self.assertEqual(
            execution.service.code,
            "pressing_article",
        )

        link = ServiceExecutionItem.objects.get(
            order_item=item
        )

        self.assertEqual(
            link.service_execution_id,
            execution.id,
        )

    def test_multiservice_creates_multiple_executions(self):
        """
        Pressing + retouche doivent produire deux
        ServiceExecution distinctes dans l'ordre de résolution.
        """
        response = self._post_order(
            phone="0700000002",
            name="Client Multi",
            service_items=[
                self.pressing_item,
                self.retouche_item,
            ],
            designations=[
                "Chemise",
                "Ourlet pantalon",
            ],
            quantities=[
                "1",
                "1",
            ],
            unit_prices=[
                "500",
                "1000",
            ],
        )

        self.assertEqual(response.status_code, 302)

        order = Order.objects.get(
            customer__phone="0700000002"
        )

        self.assertFalse(order.is_draft)

        self.assertEqual(
            order.items.count(),
            2,
        )

        executions = list(
            ServiceExecution.objects
            .filter(order=order)
            .order_by("sequence_index", "id")
        )

        self.assertEqual(
            len(executions),
            2,
        )

        self.assertEqual(
            [e.service.code for e in executions],
            [
                "pressing_article",
                "retouche_simple",
            ],
        )

        for item in order.items.all():
            self.assertTrue(
                ServiceExecutionItem.objects.filter(
                    order_item=item
                ).exists()
            )

    def test_no_items_creates_nothing(self):
        """
        Aucune ligne commerciale valide :
        aucune Order ne doit être créée.
        """
        response = self.client.post(
            reverse("portal:public_create_order"),
            data={
                "client_phone": "0700000003",
                "client_name": "Client Vide",
                "client_address": "Riviera 3",
                "service_id[]": [""],
                "designation[]": [""],
                "quantity[]": ["0"],
                "unit_price[]": ["0"],
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            "Ajoute au moins une prestation",
        )

        self.assertFalse(
            Order.objects.filter(
                customer__phone="0700000003"
            ).exists()
        )

    def test_finalization_happens_before_assignment(self):
        """
        Contrat critique :
        finalisation V2 AVANT toute assignation.
        """
        call_order = []

        def fake_finalize(*, order):
            call_order.append("finalize")

            # Simule le contrat réel : une exécution existe
            # avant le retour de finalize.
            return tuple()

        def fake_laundry(order):
            call_order.append("laundry")
            return None

        def fake_delivery(order):
            call_order.append("delivery")
            return None

        with patch(
            "portal.views.finalize_commercial_order",
            side_effect=fake_finalize,
        ), patch(
            "portal.views.auto_assign_laundry",
            side_effect=fake_laundry,
        ), patch(
            "portal.views.auto_assign_delivery",
            side_effect=fake_delivery,
        ):
            response = self._post_order(
                phone="0700000004",
                name="Client Ordre",
                service_items=[self.pressing_item],
                designations=["Chemise"],
                quantities=["1"],
                unit_prices=["500"],
            )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            call_order,
            [
                "finalize",
                "laundry",
                "delivery",
            ],
        )

    def test_catalog_resolution_error_rolls_back_order(self):
        """
        Si le catalogue V2 ne fournit pas le Service requis :
        - erreur utilisateur ;
        - rollback de l'Order ;
        - aucune ServiceExecution.
        """
        self.pressing_service.delete()

        response = self._post_order(
            phone="0700000005",
            name="Client Erreur",
            service_items=[self.pressing_item],
            designations=["Chemise"],
            quantities=["1"],
            unit_prices=["500"],
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            "ne peut pas encore être confirmée",
        )

        self.assertFalse(
            Order.objects.filter(
                customer__phone="0700000005"
            ).exists()
        )

        self.assertFalse(
            ServiceExecution.objects.exists()
        )
