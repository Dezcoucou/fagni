
from django.urls import path, include
from . import views

app_name = "orders"

urlpatterns = [
    # Liste & création
    path("", views.orders_list, name="list"),
    path("create/", views.create, name="create"),

    # Détail / édition / suppression simple
    path("<int:order_id>/", views.detail, name="detail"),
    path("<int:order_id>/status/", views.update_status, name="update_status"),

    path("<int:order_id>/edit/", views.update, name="update"),
    path("<int:order_id>/delete/", views.delete, name="delete"),

    # Tableau OPS (collecte / lavage / livraison)
    path("ops-dashboard/", views.ops_dashboard, name="ops_dashboard"),
    path(
        "ops-dashboard/<int:order_id>/<str:action>/",
        views.ops_update_step,
        name="ops_update_step",
    ),

    # Dashboard global des commandes
    path("dashboard/", views.orders_dashboard, name="dashboard"),

    # Exports commandes
    path("export/csv/", views.export_orders_csv, name="export_orders_csv"),
    path("export/xlsx/", views.export_orders_xlsx, name="export_orders_xlsx"),

    # Clients / mini CRM
    path("customers/", views.customers_list, name="customers_list"),
    path(
        "customers/export/csv/",
        views.export_customers_csv,
        name="export_customers_csv",
    ),
    path(
        "customers/export/xlsx/",
        views.export_customers_xlsx,
        name="export_customers_xlsx",
    ),
    path(
        "customers/<int:customer_id>/",
        views.orders_by_customer,
        name="orders_by_customer",
    ),

    # Lookup client (API)
    path("client-lookup/", views.client_lookup, name="client_lookup"),

    # Tickets PDF (A4 + thermique)
    path(
        "<int:order_id>/ticket/",
        views.order_ticket_pdf,
        name="order_ticket_pdf",
    ),

    path(
        "<int:order_id>/ticket-thermal/",
        views.order_ticket_thermal_pdf,
        name="order_ticket_thermal_pdf",
    ),

    # Dashboard financier
    path("finance-dashboard/", views.finance_dashboard, name="finance_dashboard"),
    path(
        "finance/export/xlsx/",
        views.export_finance_xlsx,
        name="export_finance_xlsx",
    ),

    # Top clients (CSV + XLSX)
    path(
        "top-clients/export/csv/",
        views.export_top_clients_csv,
        name="export_top_clients_csv",
    ),
    path(
        "top-clients/export/xlsx/",
        views.export_top_clients_xlsx,
        name="export_top_clients_xlsx",
    ),

    # Changement simple de statut
    path(
        "<int:order_id>/status/change/",
        views.change_status,
        name="change_status",
    ),

    # Tableau de bord livreurs
    path("drivers/dashboard/", views.driver_dashboard, name="driver_dashboard"),

    # HUB LIVREUR
    path("driver/hub/", views.driver_hub, name="driver_hub"),
    # Application mobile livreurs
    path("driver/app/", views.driver_app, name="driver_app"),
    path("driver-app/data/", views.driver_app_data, name="driver_app_data"),
    path("driver/me/", views.driver_me_app, name="driver_me_app"),
    path("driver/me/data/", views.driver_me_data, name="driver_me_data"),
    path("driver/leaderboard/", views.driver_leaderboard, name="driver_leaderboard"),
    path("driver/me/history/", views.driver_history_me, name="driver_history_me"),
    path("driver/me/order/<int:order_id>/", views.driver_order_detail, name="driver_order_detail_me"),
    path(
        "drivers/app/leg/<int:leg_id>/<str:action>/",
        views.driver_leg_action,
        name="driver_leg_action",
    ),

    path("driver/me/update-location/", views.driver_update_location, name="driver_update_location"),
    path("driver/map/", views.driver_map, name="driver_map"),

    path(
        "driver/orders/<int:order_id>/",
        views.driver_order_detail,
        name="driver_order_detail",
    ),

    path(
        "driver/performance/<int:driver_id>/",
        views.driver_performance,
        name="driver_performance",
    ),

    path(
        "driver-app/export/",
        views.driver_app_export_csv,
        name="driver_app_export_csv",
    ),

    path("accounts/", include("django.contrib.auth.urls")),

    # --- Scan QR pour accéder à la commande ---
    path(
        "scan/<str:order_code>/",
        views.order_scan_redirect,
        name="order_scan_redirect",
    ),

    path(
        "driver/orders/<int:order_id>/timeline/<str:action>/",
        views.driver_order_timeline_action,
        name="driver_timeline_action",
    ),

    path(
        "driver/kpi/",
        views.driver_kpi,
        name="driver_kpi",
    ),

    path(
        "driver/export/csv/",
        views.driver_orders_csv,
        name="driver_orders_csv",
    ),
]

