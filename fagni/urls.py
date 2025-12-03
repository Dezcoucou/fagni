from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect
from django.views.generic import RedirectView


# ============================
#  VUE HOME (redirige)
# ============================
def home(request):
    """
    Page d'accueil FAGNI :
    Pour l'instant, on redirige tout le monde vers la liste des commandes.
    (Staff et autres pourront ensuite naviguer vers le dashboard, l'app livreur, etc.)
    """
    return redirect("orders:list")


urlpatterns = [
    # Admin Django
    path("admin/", admin.site.urls),

    # 🏠 Accueil
    path("", home, name="home"),

    # 📊 Dashboard index (namespace dashboard)
    path("dashboard/", include(("dashboard.urls", "dashboard"), namespace="dashboard")),

    # Routes métier FAGNI
    path("orders/", include(("orders.urls", "orders"), namespace="orders")),
    path("mlm/", include(("mlm.urls", "mlm"), namespace="mlm")),

    # 🔐 Auth Django (login / logout / password reset, etc.)
    path("accounts/", include("django.contrib.auth.urls")),

    # 🔁 Compatibilité ancienne URL : /portal/commande/
    path(
        "portal/commande/",
        RedirectView.as_view(pattern_name="orders:create", permanent=False),
        name="portal_commande_legacy",
    ),

    path("wallets/", include("wallets.urls")),
]

# Fichiers médias en dev
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
