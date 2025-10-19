from django.shortcuts import render
from django.http import HttpResponse
from io import BytesIO
from openpyxl import Workbook
import csv
from orders.models import Order

def index(request):
    return render(request, "dashboard/analytics.html")

def export_csv(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="commandes.csv"'
    writer = csv.writer(response)
    writer.writerow(["ID", "Client", "Montant", "Date"])
    for o in Order.objects.select_related('customer').order_by('-created_at'):
        writer.writerow([o.id, o.customer.name if o.customer else "", int(o.total or 0), o.created_at.strftime("%Y-%m-%d %H:%M")])
    return response

def export_xlsx(request):
    wb = Workbook()
    ws = wb.active
    ws.title = "Commandes"
    ws.append(["ID", "Client", "Montant", "Date"])
    for o in Order.objects.select_related('customer').order_by('-created_at'):
        ws.append([o.id, o.customer.name if o.customer else "", int(o.total or 0), o.created_at.strftime("%Y-%m-%d %H:%M")])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(buf.getvalue(), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="commandes.xlsx"'
    return response
