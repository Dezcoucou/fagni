from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    # Liste & création
    path("", views.orders_list, name="list"),
    path("create/", views.create, name="create"),

    # Détail / édition / suppression simple
    path("<int:order_id>/", views.detail, name="detail"),
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
    # Alias historique : 'orders:ticket' utilisé dans tes templates
    path(
        "<int:order_id>/ticket/",
        views.order_ticket_pdf,
        name="ticket",
    ),
    # Nom "officiel" si on veut l'utiliser ailleurs proprement
    path(
        "<int:order_id>/ticket-pdf/",
        views.order_ticket_pdf,
        name="order_ticket_pdf",
    ),
    path(
        "<int:order_id>/ticket-thermal/",
        views.order_ticket_thermal_pdf,
        name="order_ticket_thermal_pdf",
    ),

    # Tableau de bord livreurs
    path("drivers/dashboard/", views.driver_dashboard, name="driver_dashboard"),

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
]
