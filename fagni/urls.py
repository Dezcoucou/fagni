from django.contrib import admin
from django.urls import path, include
from fagni.views import home, landing_riviera3

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", home, name="home"),
    path("riviera3/", landing_riviera3),
    path("dashboard/", include("dashboard.urls")),
    path("orders/", include(("orders.urls", "orders"), namespace="orders")),
    path("mlm/", include(("mlm.urls", "mlm"), namespace="mlm")),
]
