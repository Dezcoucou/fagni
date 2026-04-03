from decimal import Decimal

from django.test import Client, TestCase
from django.urls import reverse

from orders.models import Customer, Order
from orders.presenters import build_order_display_summary, build_order_finance_summary


class PresenterAndClientFinanceTests(TestCase):
    def setUp(self):
        self.client_http = Client()

        self.customer = Customer.objects.create(
            name="Client Test",
            phone="0700000001",
            address="Cocody Riviera 4"
        )

        self.order_bag = Order.objects.create(
            customer=self.customer,
            code="TESTBAG1",
            status="pending",
            payment_status="unpaid",
            pricing_mode="bag",
            bag_size="medium",
            delivery_mode="standard",
            is_draft=False,
            amount_paid=Decimal("0"),
        )

        try:
            self.order_bag.update_financials(save=True)
        except Exception:
            pass

        self.client_http.cookies["client_phone"] = self.customer.phone

    def test_presenter_display_summary_bag(self):
        summary = build_order_display_summary(self.order_bag)

        self.assertEqual(summary["pricing_mode"], "bag")
        self.assertTrue(summary["is_bag"])
        self.assertFalse(summary["is_item"])
        self.assertEqual(summary["bag_size"], "medium")
        self.assertEqual(summary["bag_label"], "Sac moyen")
        self.assertEqual(summary["pricing_label"], "Commande en sac")
        self.assertIn("volumineux", summary["exclusions_label"].lower())

    def test_presenter_finance_summary_bag(self):
        finance = build_order_finance_summary(self.order_bag)

        self.assertGreaterEqual(finance["prestation_total"], Decimal("0"))
        self.assertGreaterEqual(finance["delivery_fee_client"], Decimal("0"))
        self.assertGreaterEqual(finance["service_fee_client_ttc"], Decimal("0"))
        self.assertGreaterEqual(finance["total_client_ttc"], Decimal("0"))
        self.assertEqual(finance["amount_paid"], Decimal("0"))
        self.assertEqual(
            finance["amount_remaining"],
            finance["total_client_ttc"] - finance["amount_paid"]
        )

    def test_client_order_detail_json_matches_finance_summary(self):
        finance = build_order_finance_summary(self.order_bag)

        url = reverse("orders:client_order_detail_json", args=[self.order_bag.id])
        resp = self.client_http.get(url)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        self.assertTrue(data["ok"])
        self.assertEqual(
            Decimal(str(data["amounts"]["total_ttc"])),
            finance["total_client_ttc"]
        )
        self.assertEqual(
            Decimal(str(data["amounts"]["amount_paid"])),
            finance["amount_paid"]
        )
        self.assertEqual(
            Decimal(str(data["amounts"]["amount_due"])),
            finance["amount_remaining"]
        )

    def test_client_order_pay_cash_partial(self):
        finance_before = build_order_finance_summary(self.order_bag)
        total_before = finance_before["total_client_ttc"]

        url = reverse("orders:client_order_pay_cash", args=[self.order_bag.id])
        resp = self.client_http.post(url, {
            "amount": "5000",
            "note": "Acompte test",
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

        self.order_bag.refresh_from_db()
        finance_after = build_order_finance_summary(self.order_bag)

        self.assertEqual(self.order_bag.amount_paid, Decimal("5000"))
        self.assertEqual(getattr(self.order_bag, "payment_status", None), "partial")
        self.assertEqual(finance_after["total_client_ttc"], total_before)
        self.assertEqual(finance_after["amount_paid"], Decimal("5000"))
        self.assertEqual(
            finance_after["amount_remaining"],
            total_before - Decimal("5000")
        )

        self.assertEqual(
            Decimal(str(data["amounts"]["amount_paid"])),
            Decimal("5000")
        )
        self.assertEqual(
            Decimal(str(data["amounts"]["amount_remaining"])),
            total_before - Decimal("5000")
        )
