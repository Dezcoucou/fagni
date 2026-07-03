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


import os
import time
from django.http import JsonResponse

_worker_pid = os.getpid()
_worker_started_at = time.time()


def _get_git_commit():
    try:
        from django.conf import settings
        git_dir = settings.BASE_DIR / ".git"
        with open(git_dir / "HEAD") as f:
            head = f.read().strip()
        if head.startswith("ref:"):
            ref_path = git_dir / head.split(" ", 1)[1]
            with open(ref_path) as f:
                return f.read().strip()
        return head
    except Exception as e:
        return f"unknown ({e})"


_git_commit = _get_git_commit()


def api_health(request):
    return JsonResponse({
        "commit": _git_commit,
        "pid": _worker_pid,
        "worker_started_at": _worker_started_at,
        "now": time.time(),
    })
