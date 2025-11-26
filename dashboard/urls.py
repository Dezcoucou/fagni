from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("analytics/", views.analytics, name="analytics"),
    path("top-clients/", views.top_clients, name="top_clients"),
]
