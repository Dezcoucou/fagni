from django.shortcuts import redirect, render
from django.contrib.auth.decorators import login_required

from orders.views import orders_dashboard


def home(request):
    """
    Vue d'accueil FAGNI.

    Comportement attendu par les tests :
    - Anonyme       : redirection vers /orders/
    - Utilisateur   : redirection vers /orders/
    - Staff (admin) : redirection vers /dashboard/
    """
    if request.user.is_authenticated:
        if request.user.is_staff:
            return redirect("/dashboard/")
        return redirect("/orders/")

    return redirect("/orders/")


@login_required
def dashboard(request):
    return orders_dashboard(request)


def landing_riviera3(request):
    return render(request, "landing_riviera3.html")
