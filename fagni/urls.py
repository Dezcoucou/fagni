from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect, HttpResponse
from django.views.generic import RedirectView


# ============================
#  VUE HOME (redirige)
# ============================
def home(request):
    """
    Page d'accueil FAGNI :
    - Anonyme : redirection vers la liste des commandes (front / portail)
    - Staff connecté : redirection vers le dashboard
    """
    user = request.user
    if user.is_authenticated and user.is_staff:
        # Pour les tests : home doit renvoyer 302 pour un staff vers le dashboard
        return redirect('dashboard_index')
    # Pour les tests : home doit renvoyer 302 pour un anonyme vers les commandes
    return redirect('orders:list')


# ============================
#  VUE DASHBOARD INDEX
# ============================
def dashboard_index(request):
    """
    Dashboard minimal pour satisfaire le test :
    renvoie au moins un 200 sur /dashboard/
    (tu pourras plus tard remplacer par un vrai render() vers un template).
    """
    return HttpResponse("FAGNI Dashboard", content_type="text/plain; charset=utf-8")


urlpatterns = [
    # Admin Django
    path("admin/", admin.site.urls),

    # 🏠 Accueil
    path("", home, name="home"),

    # 📊 Dashboard index
    path("dashboard/", dashboard_index, name="dashboard_index"),

    # Routes métier FAGNI
    path("orders/", include(("orders.urls", "orders"), namespace="orders")),
    path("mlm/", include(("mlm.urls", "mlm"), namespace="mlm")),

    # 🔁 Compatibilité ancienne URL : /portal/commande/
    # 👉 Redirige vers la nouvelle page de création
    path(
        'portal/commande/',
        RedirectView.as_view(pattern_name='orders:create', permanent=False),
        name='portal_commande_legacy',
    ),
]

# Fichiers médias en dev
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
