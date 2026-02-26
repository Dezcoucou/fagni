from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect, render
from django.views.generic import RedirectView
from django.http import HttpResponse
from django.contrib.staticfiles.storage import staticfiles_storage

def chrome_devtools_wellknown(_request):
    return HttpResponse(status=204)


# ============================
#  VUE HOME (menu MVP)
# ============================
def home(request):
    """
    Page d'accueil FAGNI (MVP) :
    - Staff → dashboard
    - Sinon → landing publique (Client / Blanchisseur / Livreur)
    + Affiche les offres depuis l'admin (ServiceCategory/ServiceItem)
    """
    user = getattr(request, "user", None)
    if user and user.is_authenticated and user.is_staff:
        return redirect("dashboard:index")

    # ✅ Offres pilotables depuis l'admin
    from orders.models import ServiceCategory, ServiceItem

    # Slugs recommandés (tu les crées dans l’admin)
    laundry_slug = "blanchisserie-formules"
    pressing_slug = "pressing"

    laundry_cat = ServiceCategory.objects.filter(slug=laundry_slug).first()
    pressing_cat = ServiceCategory.objects.filter(slug=pressing_slug).first()

    laundry_offers = []
    pressing_offers = []

    if laundry_cat:
        laundry_offers = list(
            ServiceItem.objects.filter(category=laundry_cat, is_active=True).order_by("default_price", "name")
        )
    if pressing_cat:
        pressing_offers = list(
            ServiceItem.objects.filter(category=pressing_cat, is_active=True).order_by("default_price", "name")
        )

    return render(
        request,
        "home.html",
        {
            "laundry_offers": laundry_offers,
            "pressing_offers": pressing_offers,
        },
    )


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

    path("api/", include("api.urls")),

    path(".well-known/appspecific/com.chrome.devtools.json", chrome_devtools_wellknown),

    path("favicon.ico", RedirectView.as_view(url=staticfiles_storage.url("favicon.ico"), permanent=False)),
]

# Fichiers médias en dev
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
