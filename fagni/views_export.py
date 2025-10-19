import csv
from django.http import HttpResponse
from orders.models import Order

def export_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="orders.csv"'
    w = csv.writer(response)
    w.writerow(['Code','Client','Téléphone','Article','PU','Qté','Statut','Créée'])
    for o in Order.objects.order_by('-created_at'):
        w.writerow([o.code,o.client,o.phone,o.item,o.unit_price,o.quantity,o.status,o.created_at])
    return response
