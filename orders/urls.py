from django.urls import path, include
from django.views.generic import RedirectView

from . import views
from orders import views as views_ops

app_name = "orders"

urlpatterns = [
    # --- LOT_2_31_PAYMENT_UI_ROUTE_OK ---
    path("client/orders/<int:order_id>/payment-ui/", views.client_order_pay_cash, name="client_order_payment_ui"),

    # Liste & création (Back-office)
    path("", views.orders_list, name="list"),
    path("create/", views.create, name="create"),

    # Détail / édition / suppression simple (Back-office)
    path("<int:order_id>/", views.detail, name="detail"),
    path("<int:order_id>/live/", views.order_live_status, name="order_live"),
    path("<int:order_id>/status/", views.update_status, name="update_status"),
    path("<int:order_id>/edit/", views.update, name="update"),
    path("<int:order_id>/delete/", views.delete, name="delete"),

    # ✅ Encaissement (Back-office)
    path("<int:order_id>/pay/", views.order_mark_paid, name="order_mark_paid"),

    # Tableau OPS (collecte / lavage / livraison)
    path("ops-dashboard/", views.ops_dashboard, name="ops_dashboard"),

    path("ops/map/", views.driver_map, name="ops_driver_map"),
    path("ops/map/data/", views.driver_map_data, name="ops_driver_map_data"),

    # 📅 Planning OPS
    path("ops-planning/", views.ops_planning, name="ops_planning"),

    path("ops-weighing/<int:order_id>/resolve/", views.ops_weighing_resolve, name="ops_weighing_resolve"),

    path("ops-dashboard/<int:order_id>/<str:action>/", views.ops_update_step, name="ops_update_step"),

    # Dashboard global des commandes
    path("dashboard/", views.orders_dashboard, name="dashboard"),
    path("portal/dashboard/", views.portal_dashboard, name="portal_dashboard"),

    # Exports commandes
    path("export/csv/", views.export_orders_csv, name="export_orders_csv"),
    path("export/xlsx/", views.export_orders_xlsx, name="export_orders_xlsx"),

    # Clients / mini CRM (Back-office)
    path("customers/", views.customers_list, name="customers_list"),
    path("customers/export/csv/", views.export_customers_csv, name="export_customers_csv"),
    path("customers/export/xlsx/", views.export_customers_xlsx, name="export_customers_xlsx"),
    path("customers/<int:customer_id>/", views.orders_by_customer, name="orders_by_customer"),

    # Lookup client (API)
    path("client-lookup/", views.client_lookup, name="client_lookup"),

    # ============================
    # ✅ APP CLIENT (V1)
    # ============================
    path("client/", views.client_login, name="client_login"),
    path("client/logout/", views.client_logout, name="client_logout"),
    path("client/home/", views.client_home, name="client_home"),
    path("client/new/", views.client_new_order, name="client_new_order"),
    path("client/new/step-2/<int:order_id>/", views.client_new_order_step2, name="client_new_order_step2"),
    path("client/new/step-3/<int:order_id>/", views.client_new_order_step3, name="client_new_order_step3"),
    path("client/new/step-4/<int:order_id>/", views.client_new_order_step4, name="client_new_order_step4"),
    path("client/orders/<int:order_id>/", views.client_order_detail, name="client_order_detail"),
    path("client/orders/<int:order_id>/live/", views.client_order_live_status, name="client_order_live"),
    path("client/orders/<int:order_id>/rating/", views.client_order_rating, name="client_order_rating"),
    path("client/orders/<int:order_id>/evidence/upload/", views.client_order_evidence_upload, name="client_order_evidence_upload"),
    # ✅ Paiement client (V1)
    path("client/orders/<int:order_id>/pay/simulate/", views.client_order_pay_simulate, name="client_order_pay_simulate"),
    path("client/orders/<int:order_id>/pay/cash/", views.client_order_pay_cash, name="client_order_pay_cash"),
    path("client/orders/<int:order_id>/pay/wave/", views.client_order_pay_wave_page, name="client_order_pay_wave_page"),


    # --- Items (lignes de commande) côté client ---
    path("client/orders/<int:order_id>/items/new/", views.client_order_item_new, name="client_order_item_new"),
    path("client/orders/<int:order_id>/items/<int:item_id>/edit/", views.client_order_item_edit, name="client_order_item_edit"),
    path("client/orders/<int:order_id>/items/<int:item_id>/delete/", views.client_order_item_delete, name="client_order_item_delete"),

    # Tickets PDF (A4 + thermique)
    path("<int:order_id>/ticket/", views.order_ticket_pdf, name="order_ticket_pdf"),
    path("<int:order_id>/ticket-thermal/", views.order_ticket_thermal_pdf, name="order_ticket_thermal_pdf"),

    # Facture PDF (A4)
    path("<int:order_id>/invoice/", views.order_invoice_pdf, name="order_invoice_pdf"),

    # Dashboard financier
    path("finance-dashboard/", views.finance_dashboard, name="finance_dashboard"),
    path("finance/export/xlsx/", views.export_finance_xlsx, name="export_finance_xlsx"),

    # Top clients (CSV + XLSX)
    path("top-clients/export/csv/", views.export_top_clients_csv, name="export_top_clients_csv"),
    path("top-clients/export/xlsx/", views.export_top_clients_xlsx, name="export_top_clients_xlsx"),

    # Changement simple de statut
    path("<int:order_id>/status/change/", views.change_status, name="change_status"),

    # Tableau de bord livreurs
    path("drivers/dashboard/", views.driver_dashboard, name="driver_dashboard"),

    # HUB LIVREUR
    path("driver/hub/", views.driver_hub, name="driver_hub"),

    # ✅ Legacy / compat : /orders/driver/orders/ (ancienne "liste") → hub
    path(
        "driver/orders/",
        RedirectView.as_view(pattern_name="orders:driver_hub", permanent=False),
        name="driver_orders_legacy",
    ),

    # Application mobile livreurs
    path("driver-app/", views.driver_app, name="driver_app"),
    path("driver/app/", views.driver_app_alias, name="driver_app_legacy"),
    path("driver-app/data/", views.driver_app_data, name="driver_app_data"),
    path("driver/me/", views.driver_me_app, name="driver_me_app"),
    path("driver/me/data/", views.driver_me_data, name="driver_me_data"),
    path("driver/leaderboard/", views.driver_leaderboard, name="driver_leaderboard"),
    path("driver/me/history/", views.driver_history_me, name="driver_history_me"),
    path("driver/missions/", views.driver_missions_history, name="driver_missions_history"),

    path("drivers/app/leg/<int:leg_id>/<str:action>/", views.driver_leg_action, name="driver_leg_action"),

    path("driver/me/update-location/", views.driver_update_location, name="driver_update_location"),
    path("driver/map/", views.driver_map, name="driver_map"),
    path("driver/map/data/", views.driver_map_data, name="driver_map_data"),

    path("driver/orders/<int:order_id>/", views.driver_order_detail, name="driver_order_detail"),
    path("driver/orders/<int:order_id>/live/", views.driver_order_live_status, name="driver_order_live"),

    path("driver/performance/", views.driver_performance_me, name="driver_performance_me"),
    path("driver/performance/<int:driver_id>/", views.driver_performance, name="driver_performance"),

    path("driver-app/export/", views.driver_app_export_csv, name="driver_app_export_csv"),
    path("driver-app/export/xlsx/", views.driver_app_export_xlsx, name="driver_app_export_xlsx"),

    path("accounts/", include("django.contrib.auth.urls")),

    # --- Scan QR pour accéder à la commande ---
    path("scan/<str:order_code>/", views.order_scan_redirect, name="order_scan_redirect"),

    path("driver/orders/<int:order_id>/timeline/<str:action>/", views.driver_order_timeline_action, name="driver_timeline_action"),

    path("driver/kpi/", views.driver_kpi, name="driver_kpi"),
    path("driver/export/csv/", views.driver_orders_csv, name="driver_orders_csv"),
    path("driver/wallet/", views.driver_wallet, name="driver_wallet"),

    # LIVE JSON pour la map OPS (Lot 4.9)
    path("ops-dashboard/drivers-live/", views.ops_drivers_live, name="ops_drivers_live"),


    # ============================
    # ✅ MVP PRESSING — Pesée + preuves photo (LIVREUR)
    # ============================

    # Livreur (saisie pesée + upload photos preuves)
    path("driver/weighing/<int:order_id>/", views.driver_weighing, name="driver_weighing"),
    path("driver/evidence/<int:order_id>/upload/", views.driver_evidence_upload, name="driver_evidence_upload"),

    # ============================
    # ✅ MVP BLANCHISSERIE V1 — Prix par article (zéro pesée)
    # ============================

    # App blanchisseur (liste)
    path("laundry/app/", views.laundry_app, name="laundry_app"),

    # Détail commande blanchisseur (sans infos client)
    path("laundry/order/<int:order_id>/", views.laundry_order_detail, name="laundry_order_detail"),

    # Blanchisserie (validation / contestation pesée)
        path("laundry/weighing/<int:order_id>/", views.laundry_weighing, name="laundry_weighing"),
    path("laundry/weighing/<int:order_id>/confirm/", views.laundry_weighing_confirm, name="laundry_weighing_confirm"),
    path("laundry/weighing/<int:order_id>/dispute/", views.laundry_weighing_dispute, name="laundry_weighing_dispute"),
    # LOT_2_18_WAVE_OPS_CONFIRM_OK
    path("ops/orders/<int:order_id>/pay/wave/confirm/", views.ops_order_confirm_wave_paid, name="ops_order_confirm_wave_paid"),

]
