from orders.partner_api import partner_login, partner_orders, partner_update_status
from orders.ops_api import ops_login, ops_dashboard, ops_assign_partner, ops_update_status, ops_assign_driver, ops_mark_paid, ops_add_partner, ops_add_driver, ops_list_partners
from orders.driver_api import api_driver_dropoff, driver_login, driver_missions, driver_confirm_pickup, driver_confirm_delivery
from orders.photo_api import driver_upload_photo, partner_upload_photo, order_photos
from orders.client_api import api_chatbot, api_login, api_home, api_orders, api_order_detail, api_pricing_bags, api_create_order, api_articles, api_wallet, api_parrainage, api_rate_order, api_register, api_report_litige, api_pricing_detail, api_order_tracking, api_cancel_order
from django.contrib import admin
from django.urls import path, include
from fagni.views import home, landing_riviera3
from django.views.generic import RedirectView
from django.contrib.staticfiles.storage import staticfiles_storage


urlpatterns = [
    # ── API CLIENT FAGNI ──────────────────────────────────
    path("api/client/auth/login/",    api_login,    name="api-client-login"),
    path("api/client/auth/register/", api_register, name="api-client-register"),
    path("api/client/chatbot/", api_chatbot, name="chatbot"),
    path("api/client/home/",       api_home,   name="api-client-home"),
    path("api/client/orders/",     api_orders,       name="api-client-orders"),
    path("api/client/orders/<int:order_id>/", api_order_detail, name="api-client-order-detail"),
    path("api/client/pricing/bags/", api_pricing_bags,  name="api-client-pricing-bags"),
    path("api/client/orders/create/",  api_create_order, name="api-client-create-order"),
    path("api/client/articles/",          api_articles,     name="api-client-articles"),
    path("api/client/wallet/",             api_wallet,       name="api-client-wallet"),
    path("api/client/parrainage/",         api_parrainage,   name="api-client-parrainage"),
    path("api/client/orders/<int:order_id>/rate/",   api_rate_order,    name="api-client-rate"),
    path("api/client/orders/<int:order_id>/litige/",    api_report_litige,    name="api-client-litige"),
    path("api/client/orders/<int:order_id>/tracking/", api_order_tracking, name="api-client-tracking"),
    path("api/client/orders/<int:order_id>/cancel/",   api_cancel_order,   name="api-client-cancel"),
    path("api/client/pricing/detail/",               api_pricing_detail, name="api-client-pricing-detail"),
    path("api/partner/login/",              partner_login,         name="api-partner-login"),
    path("api/partner/orders/",             partner_orders,        name="api-partner-orders"),
    path("api/partner/orders/<int:order_id>/status/", partner_update_status, name="api-partner-status"),
    path("api/ops/login/",                        ops_login,          name="api-ops-login"),
    path("api/ops/dashboard/",                    ops_dashboard,      name="api-ops-dashboard"),
    path("api/ops/orders/<int:order_id>/assign/", ops_assign_partner, name="api-ops-assign"),
    path("api/ops/orders/<int:order_id>/status/", ops_update_status,  name="api-ops-status"),
    path("api/ops/orders/<int:order_id>/assign-driver/", ops_assign_driver, name="api-ops-assign-driver"),
    path("api/ops/orders/<int:order_id>/mark-paid/",    ops_mark_paid,    name="api-ops-mark-paid"),
    path("api/ops/partners/",      ops_list_partners, name="api-ops-partners"),
    path("api/ops/partners/add/",  ops_add_partner,   name="api-ops-add-partner"),
    path("api/ops/drivers/add/",   ops_add_driver,    name="api-ops-add-driver"),
    path("api/driver/login/",                          driver_login,            name="api-driver-login"),
    path("api/driver/missions/",                       driver_missions,         name="api-driver-missions"),
    path("api/driver/orders/<int:order_id>/dropoff/", api_driver_dropoff, name="driver-dropoff"),
    path("api/driver/orders/<int:order_id>/pickup/",   driver_confirm_pickup,   name="api-driver-pickup"),
    path("api/driver/orders/<int:order_id>/delivery/", driver_confirm_delivery, name="api-driver-delivery"),
    path("api/driver/orders/<int:order_id>/photo/",    driver_upload_photo,    name="api-driver-photo"),
    path("api/partner/orders/<int:order_id>/photo/",   partner_upload_photo,   name="api-partner-photo"),
    path("api/ops/orders/<int:order_id>/photos/",      order_photos,           name="api-ops-photos"),

    path("admin/", admin.site.urls),
    path("", home, name="home"),
    path("riviera3/", landing_riviera3),
    path("dashboard/", include("dashboard.urls")),
    path("orders/", include(("orders.urls", "orders"), namespace="orders")),
    path("mlm/", include(("mlm.urls", "mlm"), namespace="mlm")),
    path('favicon.ico', RedirectView.as_view(url=staticfiles_storage.url('favicon.ico'))),
]

# Dev media files
from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
