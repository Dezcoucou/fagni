# fagni/urls_app.py
from django.urls import path
from api import views  # si tes vues sont dans l'app "api"

urlpatterns = [
    path('', views.home, name='home'),
    path('order/', views.order, name='order'),
    path('tracking/', views.tracking, name='tracking'),
    path('contact/', views.contact, name='contact'),
]