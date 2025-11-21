from django.urls import path
from . import views

app_name = "mlm"

urlpatterns = [
    path("finance/", views.finance_dashboard, name="finance_dashboard"),
]
