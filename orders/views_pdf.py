from django.http import HttpResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from .models import Order

def order_pdf(request, pk:int):
    order = Order.objects.get(pk=pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="commande_{order.id}.pdf"'
    c = canvas.Canvas(response, pagesize=A4)
    w, h = A4
    y = h - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, f"Bon de commande #{order.id}")
    y -= 30
    c.setFont("Helvetica", 12)
    c.drawString(40, y, f"Client: {getattr(order, 'customer_name', 'N/A')}")
    y -= 20
    c.drawString(40, y, f"Statut: {getattr(order, 'status', 'N/A')}")
    y -= 20
    c.drawString(40, y, f"Total estimé: {getattr(order, 'total_est', '0.00')} {getattr(order, 'currency', 'XOF')}")
    y -= 30
    c.setFont("Helvetica-Bold", 12); c.drawString(40, y, "Détails :"); y -= 20
    c.setFont("Helvetica", 11)
    for item in getattr(order, 'orderitem_set', []).all():
        c.drawString(50, y, f"- {item.item.name}  x{item.quantity}  = {item.estimated_price}")
        y -= 18
        if y < 60:
            c.showPage(); y = h - 60
    c.showPage()
    c.save()
    return response

# --- Export PDF par code (commande alternative) ---
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from .models import Order

def export_order_pdf(request, code:str):
    order = get_object_or_404(Order, code=code)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="commande_{order.code}.pdf"'
    c = canvas.Canvas(response, pagesize=A4)
    w, h = A4
    y = h - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, f"Bon de commande #{order.code}")
    y -= 30
    c.setFont("Helvetica", 12)
    c.drawString(40, y, f"Client : {getattr(order, 'customer_name', 'N/A')}")
    y -= 20
    c.drawString(40, y, f"Statut : {getattr(order, 'status', 'N/A')}")
    y -= 20
    c.drawString(40, y, f"Total estimé : {getattr(order, 'total_est', '0.00')} XOF")
    c.showPage()
    c.save()
    return response
