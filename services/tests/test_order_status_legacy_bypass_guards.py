import json

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from orders.models import Customer, DeliveryLeg, Order
from partners.models import LaundryPartner
from services.models import Service, ServiceCategory
from services.services import (
    create_service_execution,
    schedule_service_execution,
)


User = get_user_model()


class LegacyOrderStatusBypassGuardTests(TestCase):
    """
    Frontière d'autorité FAGNI V2.

    Dès qu'une Order possède au moins une ServiceExecution canonique :

    - Order.status est une projection ;
    - une ancienne porte métier ne peut plus imposer directement
      pending / in_progress / ready / done ;
    - la route legacy doit refuser proprement l'écriture au lieu
      de contourner ServiceExecution.

    L'annulation globale est volontairement hors périmètre de ce lot.
    """

    def setUp(self):
        self.category = ServiceCategory.objects.create(
            code="legacy-bypass-guard-category",
            name="Legacy Bypass Guard Category",
            is_active=True,
        )

        self.customer = Customer.objects.create(
            name="Client Legacy Bypass Guard",
            phone="0700099801",
            address="Riviera 3",
        )

        self.laundry = LaundryPartner.objects.create(
            name="Pressing Legacy Bypass Guard",
            phone="0700099802",
            email="legacy-bypass@example.com",
            is_active=True,
        )

        self.order = Order.objects.create(
            customer=self.customer,
            laundry_partner=self.laundry,
            status="pending",
        )

        self.service = Service.objects.create(
            code="legacy-bypass-service",
            category=self.category,
            name="Legacy Bypass Service",
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

        self.execution = create_service_execution(
            order=self.order,
            service=self.service,
        )

        schedule_service_execution(
            service_execution=self.execution,
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.status,
            "pending",
            "précondition : une exécution seulement planifiée "
            "doit projeter Order.status=pending",
        )

    def make_done_pickup(self):
        leg = DeliveryLeg.objects.create(
            order=self.order,
            leg_type="pickup",
            status="pending",
        )

        DeliveryLeg.objects.filter(pk=leg.pk).update(
            status="done",
        )

        return leg

    def make_laundry_user(self):
        return User.objects.create_user(
            username="legacy_bypass_laundry",
            email=self.laundry.email,
            password="x",
        )

    def ops_headers(self):
        token = jwt.encode(
            {
                "ops": True,
                "name": "OPS Bypass Guard",
            },
            settings.SECRET_KEY,
            algorithm="HS256",
        )

        return {
            "HTTP_AUTHORIZATION": f"Bearer {token}",
        }

    def partner_headers(self):
        token = jwt.encode(
            {
                "pid": self.laundry.id,
                "name": self.laundry.name,
            },
            settings.SECRET_KEY,
            algorithm="HS256",
        )

        return {
            "HTTP_AUTHORIZATION": f"Bearer {token}",
        }

    def assert_order_still_projected_pending(self):
        self.order.refresh_from_db()

        self.assertEqual(
            self.order.status,
            "pending",
            "une porte legacy ne doit pas pouvoir "
            "écraser la projection ServiceExecution",
        )

    def test_ops_generic_status_cannot_force_v2_order_done(self):
        response = self.client.post(
            f"/api/ops/orders/{self.order.id}/status/",
            data={
                "status": "done",
            },
            content_type="application/json",
            **self.ops_headers(),
        )

        self.assertEqual(
            response.status_code,
            409,
            "la route OPS générique doit refuser "
            "les écritures de statut sur une Order V2",
        )

        self.assert_order_still_projected_pending()

    @override_settings(AUTO_ASSIGN_RETURN_DRIVER=False)
    def test_partner_status_cannot_force_v2_order_ready(self):
        self.make_done_pickup()

        response = Client().post(
            reverse(
                "api-partner-status",
                args=[self.order.id],
            ),
            data=json.dumps(
                {
                    "status": "ready",
                }
            ),
            content_type="application/json",
            **self.partner_headers(),
        )

        self.assertEqual(
            response.status_code,
            409,
            "le pressing legacy ne doit plus être "
            "autorisé à imposer Order.ready sur V2",
        )

        self.assert_order_still_projected_pending()

    def test_laundry_update_status_cannot_start_v2_order(self):
        self.make_done_pickup()

        user = self.make_laundry_user()
        self.client.force_login(user)

        response = self.client.post(
            reverse(
                "orders:laundry_update_status",
                args=[self.order.id],
            ),
            data={
                "action": "start",
            },
        )

        self.assertEqual(
            response.status_code,
            409,
            "laundry_update_status doit refuser "
            "la modification directe d'une Order V2",
        )

        self.assert_order_still_projected_pending()

    def test_laundry_weighing_start_cannot_start_v2_order(self):
        self.make_done_pickup()

        user = self.make_laundry_user()
        self.client.force_login(user)

        response = self.client.post(
            reverse(
                "orders:laundry_weighing",
                args=[self.order.id],
            ),
            data={
                "action": "start",
            },
        )

        self.assertEqual(
            response.status_code,
            409,
            "laundry_weighing ne doit plus piloter "
            "directement Order.status sur V2",
        )

        self.assert_order_still_projected_pending()

    def test_laundry_weighing_confirm_cannot_force_v2_order_ready(self):
        self.make_done_pickup()

        user = self.make_laundry_user()
        self.client.force_login(user)

        response = self.client.post(
            reverse(
                "orders:laundry_weighing_confirm",
                args=[self.order.id],
            ),
        )

        self.assertEqual(
            response.status_code,
            409,
            "laundry_weighing_confirm ne doit pas "
            "fabriquer Order.ready sur une Order V2",
        )

        self.assert_order_still_projected_pending()

    def test_laundry_order_detail_cannot_force_v2_order_done(self):
        user = self.make_laundry_user()
        self.client.force_login(user)

        Order.objects.filter(pk=self.order.pk).update(
            wash_complete_time=timezone.now(),
        )

        response = self.client.post(
            reverse(
                "orders:laundry_order_detail",
                args=[self.order.id],
            ),
            data={
                "action": "done",
                "laundry_id": str(self.laundry.id),
            },
        )

        self.assertEqual(
            response.status_code,
            409,
            "laundry_order_detail ne doit jamais "
            "forcer directement Order.done sur V2",
        )

        self.assert_order_still_projected_pending()


    def test_partner_refuse_cannot_mutate_v2_order(self):
        original_laundry_id = self.order.laundry_partner_id
        original_notes = self.order.notes

        response = Client().post(
            reverse(
                "api-partner-refuse",
                args=[self.order.id],
            ),
            data=json.dumps({
                "raison": "Capacité insuffisante",
            }),
            content_type="application/json",
            **self.partner_headers(),
        )

        self.assertEqual(
            response.status_code,
            409,
            "le refus pressing legacy doit être bloqué "
            "sur une commande sous autorité V2",
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.status,
            "pending",
            "le refus pressing legacy ne doit pas "
            "écraser la projection V2",
        )

        self.assertEqual(
            self.order.laundry_partner_id,
            original_laundry_id,
            "le pressing ne doit pas être désaffecté "
            "par la route legacy sur une commande V2",
        )

        self.assertEqual(
            self.order.notes,
            original_notes,
            "un refus legacy bloqué ne doit produire "
            "aucun effet de bord",
        )
