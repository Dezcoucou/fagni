from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import uuid4

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from orders.admin import OrderAdmin
from orders.models import Customer, Order


class PaymentLockTests(TestCase):
    def test_cannot_flip_paid_to_unpaid_if_wallet_transactions_exist(self):
        c = Customer.objects.create(name="Test", phone="01020304", address="Test")
        o = Order.objects.create(
            customer=c,
            status="pending",
            payment_status="paid",
            total_client_ttc=Decimal("1000"),
            amount_paid=Decimal("1000"),
        )

        mock_qs = Mock()
        mock_qs.exists.return_value = True

        mock_wt_model = Mock()
        mock_wt_model.objects.filter.return_value = mock_qs

        with patch("django.apps.apps.get_model", return_value=mock_wt_model):
            o.payment_status = "unpaid"
            with self.assertRaises(ValidationError):
                o.save()


class AdminLockTests(TestCase):
    def test_admin_actions_do_not_include_mark_unpaid(self):
        rf = RequestFactory()
        request = rf.get("/admin/orders/order/")

        User = get_user_model()

        # Réutiliser un superuser déjà seedé si présent, sinon en créer un avec username unique
        admin_user = User.objects.filter(is_superuser=True).first()
        if not admin_user:
            admin_user = User.objects.create_superuser(
                username=f"admin_{uuid4().hex[:8]}",
                email="admin@example.com",
                password="adminpass123",
            )

        request.user = admin_user

        site = AdminSite()
        ma = OrderAdmin(Order, site)

        actions = ma.get_actions(request).keys()
        self.assertNotIn("admin_mark_unpaid", actions)
