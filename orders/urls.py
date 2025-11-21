from django.urls import path
from . import views
from .api import client_lookup

app_name = 'orders'

urlpatterns = [
    # API pour autocomplétion client
    path('api/client/', client_lookup, name='client_lookup'),

    path('dashboard/', views.orders_dashboard, name='dashboard'),

    # Export CSV
    path('export_top_clients_csv/', views.export_top_clients_csv, name='export_top_clients_csv'),

    path('export_orders_csv/', views.export_orders_csv, name='export_orders_csv'),

    # Liste des commandes
    path('', views.orders_list, name='list'),

    # Création de commande
    path('create/', views.create, name='create'),

    # 🔹 Dashboard OPS FAGNI
    path('ops/', views.ops_dashboard, name='ops_dashboard'),

    path('ops/drivers/', views.driver_dashboard, name='ops_drivers'),

    # 🔹 Dashboard logistique par livreur
    path('ops/drivers/', views.driver_dashboard, name='driver_dashboard'),

    path('ops/<int:order_id>/<str:action>/', views.ops_update_step, name='ops_update_step'),

    # Détail commande
    path('<int:order_id>/', views.detail, name='detail'),

    # Mise à jour commande
    path('<int:order_id>/update/', views.update, name='update'),

    # Suppression commande
    path('<int:order_id>/delete/', views.delete, name='delete'),

    path('<int:order_id>/status/', views.change_status, name='change_status'),
]
