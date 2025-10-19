import csv
from django.http import HttpResponse
from .models import Order

def export_csv(request):
    # Réponse CSV
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="orders.csv"'
    writer = csv.writer(response)
    writer.writerow(["code", "customer", "total_ttc", "status", "created_at"])

    for o in Order.objects.all().order_by("-created_at"):
        writer.writerow([o.code, o.customer, o.total_ttc, o.status, o.created_at.isoformat()])

    return response