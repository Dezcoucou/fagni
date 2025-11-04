from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.http import HttpResponse
from .views_ping import home, ping_public, ping_commandes, ping_clients

def home(request):
    return HttpResponse('FAGNI: OK - build stable')

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("public/ping/", ping_public, name="public-ping"),
    path("commandes/ping/", ping_commandes, name="commandes-ping"),
    path("clients/ping/", ping_clients, name="clients-ping"),
]

# Servir les medias en DEBUG uniquement
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
