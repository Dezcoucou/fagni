from django.urls import path
from . import views

app_name = "orders"

urlpatterns = [
    path("", views.orders_list, name="list"),            # /orders/
    path("new/", views.order_create, name="create"),     # /orders/new/
    path("success/", views.order_success, name="success"),
    path("detail/<int:pk>/", views.order_detail, name="detail"),
]
