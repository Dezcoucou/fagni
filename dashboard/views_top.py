from django.http import HttpResponse

def dashboard_top_clients(request):
    return HttpResponse("Top clients – placeholder", content_type="text/plain; charset=utf-8")

def export_top_clients_csv(request):
    resp = HttpResponse("client,total\n", content_type="text/csv; charset=utf-8")
    resp['Content-Disposition'] = 'attachment; filename="top_clients.csv"'
    return resp

def export_top_clients_xlsx(request):
    return HttpResponse("xlsx placeholder", content_type="text/plain; charset=utf-8")


def analytics_home(request):
    # Redirige vers la page "Top clients" pour le moment
    from django.shortcuts import redirect
    return redirect('dashboard:dashboard_top_clients')
