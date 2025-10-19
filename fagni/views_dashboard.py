from django.shortcuts import render

def dashboard_view(request):
    stats = {
        "total_commandes": 23,
        "revenu_total": 178500,
        "clients_actifs": 8,
    }
    return render(request, "dashboard.html", {"stats": stats})
