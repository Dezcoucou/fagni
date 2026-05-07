from orders.client_api import api_login, api_home, api_orders, api_order_detail, api_pricing_bags, api_create_order, api_articles
from django.contrib import admin
from django.urls import path, include
from fagni.views import home, landing_riviera3
from django.views.generic import RedirectView
from django.contrib.staticfiles.storage import staticfiles_storage


urlpatterns = [
    # ── API CLIENT FAGNI ──────────────────────────────────
    path("api/client/auth/login/", api_login,  name="api-client-login"),
    path("api/client/home/",       api_home,   name="api-client-home"),
    path("api/client/orders/",     api_orders,       name="api-client-orders"),
    path("api/client/orders/<int:order_id>/", api_order_detail, name="api-client-order-detail"),
    path("api/client/pricing/bags/", api_pricing_bags,  name="api-client-pricing-bags"),
    path("api/client/orders/create/",  api_create_order, name="api-client-create-order"),
    path("api/client/articles/",          api_articles,     name="api-client-articles"),

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
