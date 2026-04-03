from django.test import SimpleTestCase
from django.urls import reverse


class LaundryWeighingUrlsTests(SimpleTestCase):
    def test_orders_laundry_weighing_urls_reverse(self):
        order_id = 1

        self.assertEqual(
            reverse("orders:laundry_weighing", kwargs={"order_id": order_id}),
            f"/orders/laundry/weighing/{order_id}/",
        )
        self.assertEqual(
            reverse("orders:laundry_weighing_confirm", kwargs={"order_id": order_id}),
            f"/orders/laundry/weighing/{order_id}/confirm/",
        )
        self.assertEqual(
            reverse("orders:laundry_weighing_dispute", kwargs={"order_id": order_id}),
            f"/orders/laundry/weighing/{order_id}/dispute/",
        )
