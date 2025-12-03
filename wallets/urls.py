from django.urls import path

from . import views

app_name = "wallets"

urlpatterns = [
    path(
        "customer/<int:customer_id>/",
        views.customer_wallet_detail,
        name="customer_wallet_detail",
    ),
]
