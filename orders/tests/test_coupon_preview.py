"""
Tests de POST /api/client/coupon/preview/ - apercu du coupon avant creation
de commande (ConfirmStep, refonte NewOrder du 20 aout 2026).

Point critique verifie explicitement : un appel a /preview/ ne doit jamais
consommer un usage reel du coupon - seule la creation de commande reelle
(api_create_order) cree un CouponUsage.
"""
from datetime import timedelta
from decimal import Decimal

import jwt
from django.conf import settings
from django.test import TestCase
from django.utils import timezone

from orders.models import Coupon, CouponUsage, Customer, Order


def _token(customer):
    return jwt.encode({'cid': customer.id, 'phone': customer.phone}, settings.SECRET_KEY, algorithm='HS256')


class ApiCouponPreviewTests(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Client preview", phone="0700998811", address="Riviera 3")
        self.coupon = Coupon.objects.create(
            code="FAGNI30",
            description="Offre pilote",
            discount_type="percent",
            discount_value=Decimal("30"),
            first_order_only=True,
            max_uses_per_customer=1,
            max_total_uses=100,
            valid_from=timezone.now() - timedelta(days=1),
            valid_until=timezone.now() + timedelta(days=30),
            is_active=True,
        )

    def _post(self, coupon_code, prestation_total=6500, customer=None):
        token = _token(customer or self.customer)
        return self.client.post(
            "/api/client/coupon/preview/",
            data={"coupon_code": coupon_code, "prestation_total": prestation_total},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def test_sans_auth_refuse(self):
        response = self.client.post(
            "/api/client/coupon/preview/",
            data={"coupon_code": "FAGNI30", "prestation_total": 6500},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)

    def test_coupon_valide(self):
        response = self._post("FAGNI30")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["valid"])
        self.assertIsNone(body["error"])
        self.assertEqual(body["discount_amount"], 1950.0)

    def test_coupon_introuvable(self):
        response = self._post("CODEBIDON")
        body = response.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["error"], "coupon_introuvable")
        self.assertEqual(body["discount_amount"], 0.0)

    def test_coupon_expire(self):
        self.coupon.valid_until = timezone.now() - timedelta(days=1)
        self.coupon.save(update_fields=["valid_until"])

        response = self._post("FAGNI30")
        body = response.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["error"], "coupon_invalide_ou_expire")

    def test_plafond_total_atteint(self):
        self.coupon.max_total_uses = 1
        self.coupon.save(update_fields=["max_total_uses"])
        other_customer = Customer.objects.create(name="Autre client", phone="0700998822", address="Riviera 2")
        other_order = Order.objects.create(
            customer=other_customer, status="pending", payment_status="pending",
            total_client_ttc=Decimal("9000"), pricing_mode="item", is_draft=False,
        )
        CouponUsage.objects.create(coupon=self.coupon, customer=other_customer, order=other_order, discount_amount=Decimal("1950"))

        response = self._post("FAGNI30")
        body = response.json()
        self.assertFalse(body["valid"])
        self.assertEqual(body["error"], "coupon_invalide_ou_expire")

    def test_preview_ne_consomme_jamais_un_usage_reel(self):
        # Le point critique demande explicitement : plusieurs apercus,
        # y compris repetes par le meme client, ne doivent creer aucun
        # CouponUsage ni faire progresser les compteurs d'utilisation.
        self.assertEqual(CouponUsage.objects.count(), 0)

        for _ in range(3):
            response = self._post("FAGNI30")
            self.assertTrue(response.json()["valid"])

        self.assertEqual(CouponUsage.objects.count(), 0)

        # Un appel ulterieur (nouvel apercu) doit toujours reussir : rien
        # n'a ete consomme par les apercus precedents.
        response = self._post("FAGNI30")
        self.assertTrue(response.json()["valid"])
