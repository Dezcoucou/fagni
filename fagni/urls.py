from django.contrib import admin
from django.urls import path, include
from fagni.views import home, landing_riviera3
from django.views.generic import RedirectView
from django.contrib.staticfiles.storage import staticfiles_storage


urlpatterns = [
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
