import pytest
from django.urls import reverse

pytestmark = pytest.mark.django_db

def test_orders_laundry_weighing_urls_reverse():
    # order_id arbitraire: on teste le reverse, pas l'existence DB
    order_id = 1

    assert reverse("orders:laundry_weighing", kwargs={"order_id": order_id}) == f"/orders/laundry/weighing/{order_id}/"
    assert reverse("orders:laundry_weighing_confirm", kwargs={"order_id": order_id}) == f"/orders/laundry/weighing/{order_id}/confirm/"
    assert reverse("orders:laundry_weighing_dispute", kwargs={"order_id": order_id}) == f"/orders/laundry/weighing/{order_id}/dispute/"
