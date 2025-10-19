import csv
from django.http import HttpResponse
from .models import Order

def export_csv(request):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="commandes.csv"'
    writer = csv.writer(response)
    writer.writerow(['Code', 'Client', 'Statut', 'Total', 'Date'])
    for o in Order.objects.all():
        writer.writerow([o.code, o.customer.name, o.status, o.total_ttc, o.created_at])
    return response
