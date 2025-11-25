from django.urls import path
from . import views

app_name = "orders"

urlpatterns = [
    # Liste & CRUD de base
    path("", views.orders_list, name="list"),
    path("create/", views.create, name="create"),
    path("<int:order_id>/", views.detail, name="detail"),
    path("<int:order_id>/edit/", views.update, name="update"),
    path("orders/<int:order_id>/ticket/", views.order_ticket_pdf, name="order_ticket_pdf"),
    path("orders/<int:order_id>/ticket-thermal/", views.order_ticket_thermal_pdf, name="order_ticket_thermal_pdf"),


    path("api/client/", views.client_lookup, name="client_lookup"),

    # Dashboards
    path("dashboard/", views.orders_dashboard, name="dashboard"),
    path("ops/", views.ops_dashboard, name="ops_dashboard"),
    path("ops/<int:order_id>/<str:action>/", views.ops_update_step, name="ops_update_step"),
    path("drivers/", views.driver_dashboard, name="driver_dashboard"),
    path("finance/", views.finance_dashboard, name="finance_dashboard"),

    # Clients / mini CRM
    path("customers/", views.customers_list, name="customers_list"),
    path("customers/export/", views.export_customers_csv, name="export_customers_csv"),
    path("customer/<int:customer_id>/", views.orders_by_customer, name="orders_by_customer"),

    # Export commandes
    path("export/csv/", views.export_orders_csv, name="export_orders_csv"),

    # Top clients (CSV + alias XLSX pour le template analytics)
    path("top-clients.csv", views.export_top_clients_csv, name="export_top_clients_csv"),
    path("top-clients.xlsx", views.export_top_clients_csv, name="export_top_clients_xlsx"),

    # Changement de statut simple
    path("<int:order_id>/status/", views.change_status, name="change_status"),

    path("finance/", views.finance_dashboard, name="finance_dashboard"),

    path("finance/export/", views.export_finance_xlsx, name="finance_export"),

    path("orders/export/xlsx/", views.export_orders_xlsx, name="export_orders_xlsx"),

    path("customers/export/xlsx/", views.export_customers_xlsx, name="export_customers_xlsx"),

    path("top-clients/export/xlsx/", views.export_top_clients_xlsx, name="export_top_clients_xlsx"),
]
