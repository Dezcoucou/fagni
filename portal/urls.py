from django.urls import path
from . import views

app_name = "portal"

urlpatterns = [
    path("commande/", views.public_create_order, name="public_create_order"),
    path("merci/<str:order_code>/", views.public_thanks, name="public_thanks"),
]
