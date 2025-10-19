from django.http import HttpResponse
from django.contrib.auth.decorators import login_required
import csv
from .models import Order

@login_required
def export_csv(request):
    # Réponse CSV
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = 'attachment; filename="orders_last_30_days.csv"'

    writer = csv.writer(resp)
    writer.writerow(['code', 'client', 'montant_ttc', 'statut', 'cree_le'])

    qs = Order.objects.order_by('-created_at')[:1000]
    for o in qs:
        writer.writerow([
            getattr(o, 'code', ''), 
            getattr(getattr(o, 'client', None), 'username', '') or getattr(o, 'client_name', ''),
            getattr(o, 'total_amount_ttc', getattr(o, 'total_ttc', '')),
            getattr(o, 'status', ''),
            getattr(o, 'created_at', '')
        ])
    return resp
