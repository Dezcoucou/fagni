from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    # /dashboard/  → vue index
    path("", views.index, name="index"),

    # /dashboard/analytics/  → vue analytics
    path("analytics/", views.analytics, name="analytics"),

    # /dashboard/top-clients/  → vue top clients
    path("top-clients/", views.top_clients, name="top_clients"),
]
