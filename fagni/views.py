from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required

from orders.views import orders_dashboard


def home(request):
    """
    Vue d'accueil FAGNI.

    Comportement attendu par les tests :
    - Anonyme       : redirection vers la liste des commandes
    - Utilisateur   : redirection vers la liste des commandes
    - Staff (admin) : redirection vers le dashboard global
    """
    if request.user.is_authenticated:
        if request.user.is_staff:
            # Staff → dashboard
            return redirect("dashboard_index")
        # Utilisateur connecté non staff → liste des commandes
        return redirect("orders:list")

    # Anonyme → liste des commandes (les tests attendent un 302)
    return redirect("orders:list")


@login_required
def dashboard_index(request):
    """
    Dashboard global FAGNI.

    On réutilise la vue existante orders_dashboard
    qui renvoie le template 'orders/dashboard.html'.
    """
    return orders_dashboard(request)
