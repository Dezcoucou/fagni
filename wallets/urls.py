from django.urls import path
from . import views

app_name = "wallets"

urlpatterns = [
    path(
        "customer/<int:customer_id>/",
        views.customer_wallet_detail,
        name="customer_wallet_detail",
    ),
    path(
        "driver/wallet/",
        views.driver_wallet_dashboard,
        name="driver_wallet_dashboard",
    ),
    path(
        "laundry/wallet/",
        views.laundry_wallet_dashboard,
        name="laundry_wallet_dashboard",
    ),
]
