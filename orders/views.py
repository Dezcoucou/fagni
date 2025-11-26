import csv
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.template.loader import render_to_string
from weasyprint import HTML

from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET
from django.db.models import (
    Count,
    Q,
    Sum,
    F,
    Value,
    Max,
    DecimalField,
    ExpressionWrapper,
)
from django.db.models.functions import Coalesce, Cast
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.utils.dateparse import parse_date

from .models import (
    Order,
    Customer,
    OrderItem,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
)
from .utils import auto_assign_laundry, auto_assign_delivery
from partners.models import LaundryPartner, DeliveryPartner
from mlm.services import attach_customer_to_sponsor

from io import BytesIO
import os
import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, A6, mm
from reportlab.lib.units import mm

from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
import qrcode

# Champ décimal générique pour les expressions
DEC = DecimalField(max_digits=12, decimal_places=2)
DECIMAL_ZERO = Decimal("0")

# -------------------------------------------------
# Helpers FAGNI : parsing des lignes + calcul frais
# -------------------------------------------------
def fagni_parse_items_from_post(request):
    """
    Lit les tableaux envoyés par create.html :
    service_id[], designation[], quantity[], unit_price[]
    et renvoie (items, total_ht)
    """
    service_ids   = request.POST.getlist('service_id[]')
    designations  = request.POST.getlist('designation[]')
    quantities    = request.POST.getlist('quantity[]')
    unit_prices   = request.POST.getlist('unit_price[]')

    items = []
    total_ht = Decimal('0')

    for i in range(len(service_ids)):
        sid = service_ids[i].strip() if i < len(service_ids) else ""
        if not sid:
            continue

        designation = designations[i].strip() if i < len(designations) else ""
        q_raw = quantities[i] if i < len(quantities) else "0"
        pu_raw = unit_prices[i] if i < len(unit_prices) else "0"

        try:
            qty = int(q_raw)
        except (TypeError, ValueError):
            qty = 0

        try:
            pu = Decimal(str(pu_raw))
        except (TypeError, ValueError, ArithmeticError):
            pu = Decimal('0')

        if qty <= 0 or pu <= 0:
            continue

        line_total = pu * qty
        total_ht += line_total

        items.append({
            "service_id": sid,
            "designation": designation or "",
            "quantity": qty,
            "unit_price": pu,
            "total": line_total,
        })

    return items, total_ht


def fagni_compute_fees(total_ht: Decimal):
    """
    Règle FAGNI : frais de service et livraison.
    NB : on reste simple ici, la vraie logique distance/km
    pourra venir plus tard dans un module dédié.
    """
    if total_ht is None:
        total_ht = Decimal('0')

    # 5% du HT, min 500 si total > 0
    SERVICE_RATE = Decimal('0.05')
    SERVICE_MIN  = Decimal('500')

    if total_ht > 0:
        service_fee = total_ht * SERVICE_RATE
        if service_fee < SERVICE_MIN:
            service_fee = SERVICE_MIN
    else:
        service_fee = Decimal('0')

    # Frais de livraison : pour l’instant min fixe si total > 0
    default_delivery_min = getattr(settings, 'FAGNI_DELIVERY_MIN_FEE', '0')
    try:
        DELIVERY_MIN = Decimal(str(default_delivery_min))
    except Exception:
        DELIVERY_MIN = Decimal('0')

    delivery_fee = DELIVERY_MIN if total_ht > 0 else Decimal('0')

    grand_total = total_ht + service_fee + delivery_fee

    return {
        "total_ht": total_ht,
        "service_fee": service_fee,
        "delivery_fee": delivery_fee,
        "grand_total": grand_total,
    }


# ============================================================
#  UTILITAIRE INTERNE : ANNOTATION DES TOTAUX
# ============================================================
def _annotate_totals(qs):
    """
    Ajoute :
      - items_total = somme (quantity * unit_price) des lignes
      - total_display = total (DB) OU items_total si total est null
    """
    items_sum = Coalesce(
        Sum(Cast(F("items__quantity"), DEC) * Cast(F("items__unit_price"), DEC)),
        Value(0, output_field=DEC),
    )

    return (
        qs.select_related("customer")
        .annotate(items_total=items_sum)
        .annotate(total_display=Coalesce(F("total"), F("items_total")))
    )


# ============================================================
#  LISTE DES COMMANDES
# ============================================================
def orders_list(request):
    """
    Liste des commandes FAGNI avec :
    - filtre par statut (all / pending / in_progress / done / canceled)
    - recherche plein texte (code, client, téléphone)
    - filtre par date de création (du / au)
    """
    qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .order_by("-created_at")
    )

    # --- Statut ---
    current_status = request.GET.get("status", "all")
    valid_statuses = ("pending", "in_progress", "done", "canceled")

    if current_status in valid_statuses:
        qs = qs.filter(status=current_status)

    # --- Recherche plein texte ---
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(code__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(customer__phone__icontains=q)
        )

    # --- Filtre date (création) ---
    date_from = request.GET.get("date_from") or ""
    date_to = request.GET.get("date_to") or ""

    if date_from:
        df = parse_date(date_from)
        if df:
            qs = qs.filter(created_at__date__gte=df)

    if date_to:
        dt = parse_date(date_to)
        if dt:
            qs = qs.filter(created_at__date__lte=dt)

    # --- Stats globales (tous statuts confondus) ---
    stats_qs = Order.objects.all()
    stats = stats_qs.aggregate(
        total_count=Count("id"),
        done_total=Sum("total", filter=Q(status="done")),
        pending_count=Count("id", filter=Q(status="pending")),
        in_progress_count=Count("id", filter=Q(status="in_progress")),
        done_count=Count("id", filter=Q(status="done")),
        canceled_count=Count("id", filter=Q(status="canceled")),
    )

    context = {
        "orders": qs,
        "total_count": stats["total_count"] or 0,
        "done_total": stats["done_total"] or 0,
        "pending_count": stats["pending_count"] or 0,
        "in_progress_count": stats["in_progress_count"] or 0,
        "done_count": stats["done_count"] or 0,
        "canceled_count": stats["canceled_count"] or 0,
        "current_status": current_status or "all",
        # pour que les champs filtres restent pré-remplis
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
    }
    return render(request, "orders/orders_list.html", context)


# ============================================================
#  TABLEAU OPS FAGNI (COLLECTE / LAVAGE / LIVRAISON)
# ============================================================
def ops_dashboard(request):
    """
    Tableau de bord opérationnel FAGNI :
    - En attente
    - En cours
    - Terminées
    """
    base_qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .prefetch_related("items__photos")
    )

    pending_orders = base_qs.filter(status="pending")
    in_progress_orders = base_qs.filter(status="in_progress")
    done_orders = base_qs.filter(status="done")

    agg = done_orders.aggregate(s=Sum("total"))
    done_total = agg["s"] or Decimal("0.00")

    context = {
        "pending_orders": pending_orders,
        "in_progress_orders": in_progress_orders,
        "done_orders": done_orders,
        "done_total": done_total,
    }
    return render(request, "orders/ops_dashboard.html", context)


@require_POST
def ops_update_step(request, order_id, action):
    """
    Met à jour les timestamps opérationnels :
    - pickup, dropoff, wash_done, return, delivered
    + bascule éventuellement le statut.
    """
    order = get_object_or_404(Order, pk=order_id)
    now = timezone.now()

    if action == "pickup":
        order.pickup_time = now
        if order.status == "pending":
            order.status = "in_progress"
    elif action == "dropoff":
        order.dropoff_time = now
    elif action == "wash_done":
        order.wash_complete_time = now
    elif action == "return":
        order.return_time = now
    elif action == "delivered":
        order.delivered_time = now
        order.status = "done"

    order.save()
    return redirect("orders:ops_dashboard")


# ============================================================
#  DASHBOARD GLOBAL DES COMMANDES
# ============================================================
def orders_dashboard(request):
    base_qs = _annotate_totals(Order.objects.all())

    total_count = base_qs.count()
    pending_count = base_qs.filter(status="pending").count()
    in_progress_count = base_qs.filter(status="in_progress").count()
    done_count = base_qs.filter(status="done").count()
    canceled_count = base_qs.filter(status="canceled").count()

    done_total = (
        base_qs.filter(status="done").aggregate(total=Sum("total"))["total"]
        or Decimal("0")
    )

    if total_count > 0:
        completion_rate = (done_count / total_count) * 100
        cancel_rate = (canceled_count / total_count) * 100
    else:
        completion_rate = 0
        cancel_rate = 0

    if done_count > 0:
        avg_ticket_done = done_total / done_count
    else:
        avg_ticket_done = Decimal("0")

    if total_count > 0:
        avg_ticket_all = done_total / total_count
    else:
        avg_ticket_all = Decimal("0")

    today = timezone.localdate()
    today_qs = base_qs.filter(created_at__date=today)
    today_count = today_qs.count()
    today_done_total = (
        today_qs.filter(status="done").aggregate(total=Sum("total"))["total"]
        or Decimal("0")
    )

    start_week = today - timedelta(days=today.weekday())
    start_month = today.replace(day=1)

    week_qs = base_qs.filter(created_at__date__gte=start_week)
    week_count = week_qs.count()
    week_done_total = (
        week_qs.filter(status="done").aggregate(total=Sum("total"))["total"]
        or Decimal("0")
    )

    month_qs = base_qs.filter(created_at__date__gte=start_month)
    month_count = month_qs.count()
    month_done_total = (
        month_qs.filter(status="done").aggregate(total=Sum("total"))["total"]
        or Decimal("0")
    )

    days_param = request.GET.get("days", "7")
    try:
        days_int = int(days_param)
    except (TypeError, ValueError):
        days_int = 7
    if days_int not in (7, 14, 30):
        days_int = 7

    chart_labels = []
    chart_orders = []
    chart_totals = []

    for i in range(days_int - 1, -1, -1):
        day = today - timedelta(days=i)
        day_qs = base_qs.filter(created_at__date=day)

        chart_labels.append(day.strftime("%d/%m"))
        chart_orders.append(day_qs.count())

        day_total = (
            day_qs.filter(status="done").aggregate(total=Sum("total"))["total"]
            or Decimal("0")
        )
        chart_totals.append(float(day_total))

    top_clients = (
        Order.objects
        .filter(status="done", customer__isnull=False)
        .values("customer__name", "customer__phone")
        .annotate(
            total_spent=Sum("total"),
            orders_count=Count("id"),
        )
        .order_by("-total_spent")[:5]
    )

    top_items = (
        OrderItem.objects
        .filter(order__status="done")
        .values("designation")
        .annotate(
            total_qty=Sum("quantity"),
            total_amount=Sum("total"),
        )
        .order_by("-total_qty")[:5]
    )

    context = {
        "total_count": total_count,
        "pending_count": pending_count,
        "in_progress_count": in_progress_count,
        "done_count": done_count,
        "canceled_count": canceled_count,
        "done_total": done_total,
        "today": today,
        "today_count": today_count,
        "today_done_total": today_done_total,
        "start_week": start_week,
        "start_month": start_month,
        "week_count": week_count,
        "week_done_total": week_done_total,
        "month_count": month_count,
        "month_done_total": month_done_total,
        "current_days": days_int,
        "chart_labels": chart_labels,
        "chart_orders": chart_orders,
        "chart_totals": chart_totals,
        "top_clients": top_clients,
        "top_items": top_items,
        "completion_rate": completion_rate,
        "cancel_rate": cancel_rate,
        "avg_ticket_done": avg_ticket_done,
        "avg_ticket_all": avg_ticket_all,
    }
    return render(request, "orders/dashboard.html", context)


# ============================================================
#  EXPORT CSV COMMANDES
# ============================================================
def export_orders_csv(request):
    status = request.GET.get("status")

    qs = Order.objects.select_related("customer").all()

    valid_statuses = {"pending", "in_progress", "done", "canceled"}
    if status in valid_statuses:
        qs = qs.filter(status=status)

    response = HttpResponse(content_type="text/csv")
    filename = f"commandes_fagni_{timezone.now().strftime('%Y%m%d_%H%M')}.csv"
    response["Content-Disposition"] = f'attachment; filename=\"{filename}\"'

    writer = csv.writer(response, delimiter=";")

    writer.writerow(
        [
            "ID",
            "Code",
            "Date création",
            "Statut",
            "Client",
            "Téléphone",
            "Adresse",
            "Total (DB)",
            "Total HT",
            "TVA",
            "Total TTC",
            "Montant payé",
            "Montant dû",
        ]
    )

    for order in qs.order_by("-created_at"):
        customer = getattr(order, "customer", None)
        customer_name = getattr(customer, "name", "") if customer else ""
        customer_phone = getattr(customer, "phone", "") if customer else ""
        customer_address = getattr(customer, "address", "") if customer else ""

        try:
            total_ht = order.total_ht
        except Exception:
            total_ht = ""

        try:
            tva_amount = order.tva_amount
        except Exception:
            tva_amount = ""

        try:
            total_ttc = order.total_ttc
        except Exception:
            total_ttc = ""

        try:
            amount_paid = order.amount_paid
        except Exception:
            amount_paid = ""

        try:
            amount_due = order.amount_due
        except Exception:
            amount_due = ""

        writer.writerow(
            [
                order.id,
                order.code or "",
                order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else "",
                order.get_status_display(),
                customer_name,
                customer_phone,
                customer_address,
                order.total if hasattr(order, "total") else "",
                total_ht,
                tva_amount,
                total_ttc,
                amount_paid,
                amount_due,
            ]
        )

    return response


def export_orders_xlsx(request):
    """
    Export Excel des commandes FAGNI :
    - Reprend les filtres de la liste (status, q, date_from, date_to)
    - Onglet 1 : Synthèse
    - Onglet 2 : Détail des commandes
    """
    status = request.GET.get("status", "all")
    q = (request.GET.get("q") or "").strip()
    date_from = request.GET.get("date_from") or ""
    date_to = request.GET.get("date_to") or ""

    qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .order_by("-created_at")
    )

    valid_statuses = ("pending", "in_progress", "done", "canceled")
    if status in valid_statuses:
        qs = qs.filter(status=status)

    if q:
        qs = qs.filter(
            Q(code__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(customer__phone__icontains=q)
        )

    if date_from:
        df = parse_date(date_from)
        if df:
            qs = qs.filter(created_at__date__gte=df)

    if date_to:
        dt = parse_date(date_to)
        if dt:
            qs = qs.filter(created_at__date__lte=dt)

    orders = list(qs[:1000])  # limite de sécurité

    # Helper décimal
    def d(val):
        if isinstance(val, Decimal):
            return val
        if val in (None, "", 0):
            return Decimal("0")
        try:
            return Decimal(str(val))
        except Exception:
            return Decimal("0")

    # Stats synthèse
    total_count = len(orders)
    pending_count = sum(1 for o in orders if o.status == "pending")
    in_progress_count = sum(1 for o in orders if o.status == "in_progress")
    done_count = sum(1 for o in orders if o.status == "done")
    canceled_count = sum(1 for o in orders if o.status == "canceled")
    done_total = sum(d(getattr(o, "total", None)) for o in orders if o.status == "done")

    # Styles Excel
    wb = Workbook()
    title_font = Font(size=16, bold=True, color="FFFFFF")
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="0056B3")
    section_fill = PatternFill("solid", fgColor="FF7A00")
    label_font = Font(bold=True)
    thin_border = Border(
        left=Side(style="thin", color="DDDDDD"),
        right=Side(style="thin", color="DDDDDD"),
        top=Side(style="thin", color="DDDDDD"),
        bottom=Side(style="thin", color="DDDDDD"),
    )
    center = Alignment(horizontal="center", vertical="center")
    right = Alignment(horizontal="right", vertical="center")
    left = Alignment(horizontal="left", vertical="center")
    wrap = Alignment(wrap_text=True, vertical="top")

    # ---------- Onglet 1 : Synthèse ----------
    ws1 = wb.active
    ws1.title = "Synthèse"

    ws1.merge_cells("A1:D1")
    cell_title = ws1["A1"]
    cell_title.value = "FAGNI – Export commandes"
    cell_title.font = title_font
    cell_title.fill = header_fill
    cell_title.alignment = center

    ws1["A3"] = "Généré le"
    ws1["A3"].font = label_font
    ws1["B3"] = timezone.localtime().strftime("%d/%m/%Y %H:%M")

    ws1["A4"] = "Statut"
    ws1["A4"].font = label_font
    ws1["B4"] = status if status in valid_statuses else "Tous"

    ws1["A5"] = "Recherche"
    ws1["A5"].font = label_font
    ws1["B5"] = q or "-"

    ws1["A6"] = "Période"
    ws1["A6"].font = label_font
    if date_from or date_to:
        per = ""
        if date_from:
            per += f"du {date_from} "
        if date_to:
            per += f"au {date_to}"
        ws1["B6"] = per
    else:
        ws1["B6"] = "Toutes les dates"

    ws1.merge_cells("A8:D8")
    sec = ws1["A8"]
    sec.value = "Synthèse des commandes"
    sec.font = Font(bold=True, color="FFFFFF")
    sec.fill = section_fill
    sec.alignment = left

    data_rows = [
        ("Nombre de commandes", total_count),
        ("En attente", pending_count),
        ("En cours", in_progress_count),
        ("Terminées", done_count),
        ("Annulées", canceled_count),
        ("Total commandes terminées (Total DB)", f"{done_total} FCFA"),
    ]

    start_row = 10
    for i, (label, value) in enumerate(data_rows):
        r = start_row + i
        ws1[f"A{r}"] = label
        ws1[f"A{r}"].font = label_font
        ws1[f"B{r}"] = value

    ws1.column_dimensions["A"].width = 40
    ws1.column_dimensions["B"].width = 35

    # ---------- Onglet 2 : Commandes ----------
    ws2 = wb.create_sheet(title="Commandes")

    headers = [
        "Code",
        "Date création",
        "Statut",
        "Client",
        "Téléphone",
        "Adresse",
        "Total (DB)",
        "Total HT",
        "TVA",
        "Total TTC",
        "Montant payé",
        "Montant dû",
        "Service FAGNI",
        "Livraison",
        "Blanchisserie",
        "Livreur",
    ]

    for col_idx, head in enumerate(headers, start=1):
        c = ws2.cell(row=1, column=col_idx, value=head)
        c.font = header_font
        c.fill = header_fill
        c.alignment = center
        c.border = thin_border

    row_idx = 2

    for o in orders:
        customer = getattr(o, "customer", None)

        # champs financiers optionnels (comme dans export_orders_csv)
        try:
            total_ht = o.total_ht
        except Exception:
            total_ht = ""

        try:
            tva_amount = o.tva_amount
        except Exception:
            tva_amount = ""

        try:
            total_ttc = o.total_ttc
        except Exception:
            total_ttc = ""

        try:
            amount_paid = o.amount_paid
        except Exception:
            amount_paid = ""

        try:
            amount_due = o.amount_due
        except Exception:
            amount_due = ""

        row_vals = [
            o.code or "",
            o.created_at.strftime("%d/%m/%Y %H:%M") if o.created_at else "",
            o.get_status_display(),
            customer.name if customer else "",
            customer.phone if customer else "",
            customer.address if customer else "",
            d(getattr(o, "total", None)),
            d(total_ht) if total_ht not in ("", None) else "",
            d(tva_amount) if tva_amount not in ("", None) else "",
            d(total_ttc) if total_ttc not in ("", None) else "",
            d(amount_paid) if amount_paid not in ("", None) else "",
            d(amount_due) if amount_due not in ("", None) else "",
            d(getattr(o, "service_fee", None)),
            d(getattr(o, "delivery_fee", None)),
            o.laundry_partner.name if o.laundry_partner else "",
            o.delivery_partner.name if o.delivery_partner else "",
        ]

        for col_idx, val in enumerate(row_vals, start=1):
            c = ws2.cell(row=row_idx, column=col_idx, value=float(val) if isinstance(val, Decimal) else val)
            c.border = thin_border
            if isinstance(val, Decimal):
                c.alignment = right
                c.number_format = "#,##0"
            elif col_idx in (4, 5, 6):
                c.alignment = wrap
            else:
                c.alignment = left

        row_idx += 1

    # Largeurs colonnes
    widths = [14, 18, 16, 20, 14, 30, 14, 14, 12, 14, 14, 14, 14, 14, 18, 18]
    for i, w in enumerate(widths, start=1):
        ws2.column_dimensions[chr(64 + i)].width = w

    ws2.auto_filter.ref = ws2.dimensions

    # Réponse HTTP
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"fagni_commandes_{timezone.now().strftime('%Y%m%d_%H%M')}.xlsx"
    resp = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# ============================================================
#  LISTE DES COMMANDES D'UN CLIENT (FICHE CLIENT)
# ============================================================
def orders_by_customer(request, customer_id):
    """
    Fiche client FAGNI :
    - Infos client
    - Stats globales (nb commandes, CA, service, livraison)
    - Historique des commandes
    """
    customer = get_object_or_404(Customer, pk=customer_id)

    qs = (
        Order.objects
        .filter(customer=customer)
        .select_related("customer", "laundry_partner", "delivery_partner")
        .order_by("-created_at")
    )

    agg = qs.aggregate(
        total_orders=Count("id"),
        done_orders=Count("id", filter=Q(status="done")),
        total_amount=Coalesce(Sum("total"), Decimal("0.00")),
        total_service_fee=Coalesce(Sum("service_fee"), Decimal("0.00")),
        total_delivery_fee=Coalesce(Sum("delivery_fee"), Decimal("0.00")),
    )

    context = {
        "customer": customer,
        "orders": qs,
        "total_orders": agg["total_orders"] or 0,
        "total_done": agg["done_orders"] or 0,
        "total_amount": agg["total_amount"] or Decimal("0.00"),
        "total_service_fee": agg["total_service_fee"] or Decimal("0.00"),
        "total_delivery_fee": agg["total_delivery_fee"] or Decimal("0.00"),
    }
    return render(request, "orders/orders_by_customer.html", context)


# ============================================================
#  LISTE CLIENTS – MINI CRM
# ============================================================
def customers_list(request):
    """
    Liste des clients FAGNI (mini CRM) :
    - Recherche (nom, téléphone, adresse)
    - Filtre min_orders (nb min de commandes)
    - Stats : nb commandes, montant total, service FAGNI, livraison
    """
    q = (request.GET.get("q") or "").strip()
    min_orders = request.GET.get("min_orders") or ""

    qs = (
        Customer.objects
        .annotate(
            # ⚠️ On suppose related_name="orders" sur Order.customer
            total_orders=Count("orders", distinct=True),
            total_amount=Coalesce(Sum("orders__total"), Decimal("0.00")),
            total_service_fee=Coalesce(Sum("orders__service_fee"), Decimal("0.00")),
            total_delivery_fee=Coalesce(Sum("orders__delivery_fee"), Decimal("0.00")),
            last_order_date=Max("orders__created_at"),
        )
    )

    # 🔍 Recherche plein texte
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(address__icontains=q)
        )

    # 🔢 Filtre min_orders
    if min_orders:
        try:
            min_o = int(min_orders)
        except (ValueError, TypeError):
            min_o = 0
        if min_o > 0:
            qs = qs.filter(total_orders__gte=min_o)

    # Tri : par date de dernière commande puis nom
    qs = qs.order_by(
        F("last_order_date").desc(nulls_last=True),
        "name",
    )

    total_customers = qs.count()
    total_with_orders = qs.filter(total_orders__gt=0).count()

    context = {
        "customers": qs,
        "total_customers": total_customers,
        "total_with_orders": total_with_orders,
        "q": q,
        "min_orders": min_orders,
    }
    return render(request, "orders/customers_list.html", context)


# ============================================================
#  EXPORT CSV CLIENTS – MINI CRM
# ============================================================
def export_customers_csv(request):
    """
    Export CSV de la liste des clients avec stats agrégées.
    Les mêmes filtres (q, min_orders) que la liste HTML sont appliqués.
    """
    q = (request.GET.get("q") or "").strip()
    min_orders = request.GET.get("min_orders") or ""

    qs = (
        Customer.objects
        .annotate(
            total_orders=Count("orders", distinct=True),
            total_amount=Coalesce(Sum("orders__total"), Decimal("0.00")),
            total_service_fee=Coalesce(Sum("orders__service_fee"), Decimal("0.00")),
            total_delivery_fee=Coalesce(Sum("orders__delivery_fee"), Decimal("0.00")),
            last_order_date=Max("orders__created_at"),
        )
    )

    # mêmes filtres que la liste
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(address__icontains=q)
        )

    if min_orders:
        try:
            min_o = int(min_orders)
        except (ValueError, TypeError):
            min_o = 0
        if min_o > 0:
            qs = qs.filter(total_orders__gte=min_o)

    # Réponse CSV
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    filename = f"clients_fagni_{timezone.now().strftime('%Y%m%d_%H%M')}.csv"
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'

    # En-tête
    resp.write(
        "Nom;Téléphone;Adresse;Nb commandes;Montant total;Service FAGNI;Livraison;Dernière commande\n"
    )

    for c in qs:
        last_date_str = (
            c.last_order_date.strftime("%d/%m/%Y %H:%M")
            if c.last_order_date else ""
        )

        line = ";".join([
            (c.name or "").replace(";", ","),
            (c.phone or "").replace(";", ","),
            (c.address or "").replace(";", ","),
            str(c.total_orders or 0),
            str(c.total_amount or 0),
            str(c.total_service_fee or 0),
            str(c.total_delivery_fee or 0),
            last_date_str,
        ])
        resp.write(line + "\n")

    return resp


def export_customers_xlsx(request):
    """
    Export Excel de la liste des clients avec stats agrégées.
    Reprend les mêmes filtres (q, min_orders) que la liste HTML.
    """
    q = (request.GET.get("q") or "").strip()
    min_orders = request.GET.get("min_orders") or ""

    qs = (
        Customer.objects
        .annotate(
            total_orders=Count("orders", distinct=True),
            total_amount=Coalesce(Sum("orders__total"), Decimal("0.00")),
            total_service_fee=Coalesce(Sum("orders__service_fee"), Decimal("0.00")),
            total_delivery_fee=Coalesce(Sum("orders__delivery_fee"), Decimal("0.00")),
            last_order_date=Max("orders__created_at"),
        )
    )

    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(address__icontains=q)
        )

    if min_orders:
        try:
            min_o = int(min_orders)
        except (ValueError, TypeError):
            min_o = 0
        if min_o > 0:
            qs = qs.filter(total_orders__gte=min_o)

    qs = qs.order_by(
        F("last_order_date").desc(nulls_last=True),
        "name",
    )

    customers = list(qs)

    # Helper décimal
    def d(val):
        if isinstance(val, Decimal):
            return val
        if val in (None, "", 0):
            return Decimal("0")
        try:
            return Decimal(str(val))
        except Exception:
            return Decimal("0")

    total_customers = len(customers)
    total_with_orders = sum(1 for c in customers if (c.total_orders or 0) > 0)
    total_amount = sum(d(c.total_amount) for c in customers)
    total_service = sum(d(c.total_service_fee) for c in customers)
    total_delivery = sum(d(c.total_delivery_fee) for c in customers)

    # Styles
    wb = Workbook()
    title_font = Font(size=16, bold=True, color="FFFFFF")
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="0056B3")
    section_fill = PatternFill("solid", fgColor="FF7A00")
    label_font = Font(bold=True)
    thin_border = Border(
        left=Side(style="thin", color="DDDDDD"),
        right=Side(style="thin", color="DDDDDD"),
        top=Side(style="thin", color="DDDDDD"),
        bottom=Side(style="thin", color="DDDDDD"),
    )
    center = Alignment(horizontal="center", vertical="center")
    right = Alignment(horizontal="right", vertical="center")
    left = Alignment(horizontal="left", vertical="center")
    wrap = Alignment(wrap_text=True, vertical="top")

    # --- Synthèse ---
    ws1 = wb.active
    ws1.title = "Synthèse"

    ws1.merge_cells("A1:D1")
    t = ws1["A1"]
    t.value = "FAGNI – Export clients"
    t.font = title_font
    t.fill = header_fill
    t.alignment = center

    ws1["A3"] = "Généré le"
    ws1["A3"].font = label_font
    ws1["B3"] = timezone.localtime().strftime("%d/%m/%Y %H:%M")

    ws1["A4"] = "Recherche"
    ws1["A4"].font = label_font
    ws1["B4"] = q or "-"

    ws1["A5"] = "Min. commandes"
    ws1["A5"].font = label_font
    ws1["B5"] = min_orders or "0"

    ws1.merge_cells("A7:D7")
    s = ws1["A7"]
    s.value = "Synthèse du portefeuille clients"
    s.font = Font(bold=True, color="FFFFFF")
    s.fill = section_fill
    s.alignment = left

    lines = [
        ("Nombre total de clients", total_customers),
        ("Clients avec au moins 1 commande", total_with_orders),
        ("Montant total commandes", f"{total_amount} FCFA"),
        ("Service FAGNI cumulé", f"{total_service} FCFA"),
        ("Livraison facturée cumulée", f"{total_delivery} FCFA"),
    ]

    start_row = 9
    for i, (label, value) in enumerate(lines):
        r = start_row + i
        ws1[f"A{r}"] = label
        ws1[f"A{r}"].font = label_font
        ws1[f"B{r}"] = value

    ws1.column_dimensions["A"].width = 45
    ws1.column_dimensions["B"].width = 35

    # --- Détail clients ---
    ws2 = wb.create_sheet(title="Clients")

    headers = [
        "Nom",
        "Téléphone",
        "Adresse",
        "Nb commandes",
        "Montant total",
        "Service FAGNI",
        "Livraison",
        "Dernière commande",
    ]

    for col_idx, head in enumerate(headers, start=1):
        c = ws2.cell(row=1, column=col_idx, value=head)
        c.font = header_font
        c.fill = header_fill
        c.alignment = center
        c.border = thin_border

    row_idx = 2
    for cst in customers:
        last_date_str = (
            cst.last_order_date.strftime("%d/%m/%Y %H:%M")
            if cst.last_order_date else ""
        )
        row_vals = [
            cst.name or "",
            cst.phone or "",
            cst.address or "",
            int(cst.total_orders or 0),
            float(d(cst.total_amount)),
            float(d(cst.total_service_fee)),
            float(d(cst.total_delivery_fee)),
            last_date_str,
        ]
        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws2.cell(row=row_idx, column=col_idx, value=val)
            cell.border = thin_border
            if col_idx in (4, 5, 6, 7):
                cell.alignment = right
                if col_idx >= 5:
                    cell.number_format = "#,##0"
            elif col_idx == 3:
                cell.alignment = wrap
            else:
                cell.alignment = left
        row_idx += 1

    widths = [24, 16, 30, 14, 16, 16, 16, 20]
    for i, w in enumerate(widths, start=1):
        ws2.column_dimensions[chr(64 + i)].width = w

    ws2.auto_filter.ref = ws2.dimensions

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    filename = f"fagni_clients_{timezone.now().strftime('%Y%m%d_%H%M')}.xlsx"
    resp = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp


# ============================================================
#  CRÉATION COMMANDE
# ============================================================
@login_required
def create(request):
    """
    Création d'une commande FAGNI :
    - Client (nom, téléphone, adresse, lat/lng)
    - Lignes de prestations issues du catalogue
    - Photos multiples par ligne (photos_0, photos_1, ...)
    - Assignation automatique blanchisserie + livreur
    - Calcul frais de livraison (Haversine)
    - Rattachement éventuel à un code affilié (MLM)
    """
    service_categories = ServiceCategory.objects.all()
    service_items = ServiceItem.objects.select_related("category").all()

    # Paramètres logistiques
    logi = getattr(settings, "FAGNI_LOGISTICS", {})

    # Pour garder / pré-remplir le code affilié
    if request.method == "POST":
        affiliate_code_initial = request.POST.get("affiliate_code", "").strip()
    else:
        affiliate_code_initial = (
            (request.GET.get("aff") or "").strip()
            or (request.GET.get("ref") or "").strip()
        )

    context = {
        "service_categories": service_categories,
        "service_items": service_items,
        "error": None,
        # valeurs par défaut pour pré-remplir le formulaire en cas d'erreur
        "client_phone": request.POST.get("client_phone", "") if request.method == "POST" else "",
        "client_name": request.POST.get("client_name", "") if request.method == "POST" else "",
        "client_address": request.POST.get("client_address", "") if request.method == "POST" else "",
        "client_lat": request.POST.get("client_lat", "") if request.method == "POST" else "",
        "client_lng": request.POST.get("client_lng", "") if request.method == "POST" else "",
        # on garde aussi le code affilié saisi en cas d'erreur ou pré-rempli via ?aff= / ?ref=
        "affiliate_code": affiliate_code_initial,
        "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
        "delivery_min_fee": logi.get("client_min_fee", 1000),
        "delivery_price_per_km": logi.get("client_price_per_km", 150),
        "delivery_fixed_fee": logi.get("client_fixed_fee", 300),
    }

    if request.method == "POST":
        phone = request.POST.get("client_phone", "").strip()
        name = request.POST.get("client_name", "").strip()
        address = request.POST.get("client_address", "").strip()
        lat_raw = request.POST.get("client_lat", "").strip()
        lng_raw = request.POST.get("client_lng", "").strip()

        # 🔹 Code affilié (MLM) : prioritaire via POST, sinon via GET ?aff= / ?ref=
        affiliate_code = (
            request.POST.get("affiliate_code", "").strip()
            or (request.GET.get("aff") or "").strip()
            or (request.GET.get("ref") or "").strip()
        )
        # pour le cas d'erreur, on le remet dans le context
        context["affiliate_code"] = affiliate_code

        # 1) Validation minimale client
        if not phone or not name:
            context["error"] = "Merci de renseigner au moins le nom et le téléphone du client."
            return render(request, "orders/create.html", context)

        # 2) Création / mise à jour du client
        customer, created = Customer.objects.get_or_create(
            phone=phone,
            defaults={
                "name": name,
                "address": address,
            },
        )
        # Mise à jour systématique
        customer.name = name
        customer.address = address

        # latitude / longitude si présentes
        try:
            if lat_raw:
                customer.latitude = Decimal(lat_raw)
            if lng_raw:
                customer.longitude = Decimal(lng_raw)
        except Exception:
            customer.latitude = None
            customer.longitude = None

        customer.save()

        # 🔹 Rattachement MLM si un code affilié est présent
        if affiliate_code:
            attach_customer_to_sponsor(customer, affiliate_code)

        # 3) Création de la commande "vide"
        order = Order.objects.create(
            customer=customer,
            status="pending",
        )

        # 4) Lecture des lignes du formulaire
        service_ids = request.POST.getlist("service_id[]")
        designations = request.POST.getlist("designation[]")
        quantities = request.POST.getlist("quantity[]")
        unit_prices = request.POST.getlist("unit_price[]")

        created_any_item = False

        for idx, (sid, desc, qty_str, pu_str) in enumerate(
            zip(service_ids, designations, quantities, unit_prices)
        ):
            desc = (desc or "").strip()
            if not desc:
                continue

            # quantité
            try:
                qty = int(qty_str)
            except Exception:
                qty = 0

            # prix unitaire
            try:
                pu = Decimal(pu_str)
            except Exception:
                pu = Decimal("0.00")

            if qty <= 0 or pu <= 0:
                # on ignore les lignes non valides
                continue

            # service lié (facultatif)
            service_obj = None
            if sid:
                try:
                    service_obj = ServiceItem.objects.get(pk=int(sid))
                except (ServiceItem.DoesNotExist, ValueError, TypeError):
                    service_obj = None

            item = OrderItem.objects.create(
                order=order,
                service=service_obj,
                designation=desc,
                quantity=qty,
                unit_price=pu,
            )

            created_any_item = True

            # 5) Gestion des photos multiples pour cette ligne
            photos_field_name = f"photos_{idx}"
            files = request.FILES.getlist(photos_field_name)
            for f in files:
                OrderItemPhoto.objects.create(
                    order_item=item,
                    image=f,
                )

        # si aucune ligne valide => on annule la commande
        if not created_any_item:
            order.delete()
            context["error"] = (
                "Ajoute au moins une ligne de prestation avec quantité "
                "et prix unitaire > 0."
            )
            return render(request, "orders/create.html", context)

        # 6) Assignation automatique blanchisserie & livreur
        laundry_partner = auto_assign_laundry(order)
        if laundry_partner:
            order.laundry_partner = laundry_partner

        delivery_partner = auto_assign_delivery(order)
        if delivery_partner:
            order.delivery_partner = delivery_partner

        # 7) Calcul des frais de livraison (si une blanchisserie est affectée)
        if order.laundry_partner:
            order.delivery_fee = order.compute_delivery_fee()

        # 8) Sauvegarde finale (recalcule total + service_fee + éventuels signaux)
        order.save()

        # Redirection : liste des commandes
        return redirect("orders:list")

    # GET => affichage simple du formulaire
    return render(request, "orders/create.html", context)


@require_GET
def client_lookup(request):
    """
    API simple pour retrouver un client à partir du téléphone.
    Appelée par le formulaire de création de commande.
    """
    phone = (request.GET.get('phone') or "").strip()

    if not phone:
        return JsonResponse({"exists": False})

    # Tu peux mettre phone__icontains, startswith ou exact selon ton besoin
    qs = Customer.objects.filter(phone__startswith=phone).order_by("id")
    if not qs.exists():
        return JsonResponse({"exists": False})

    c = qs.first()

    return JsonResponse({
        "exists": True,
        "name": c.name or "",
        "phone": c.phone or "",
        "address": getattr(c, "address", "") or "",
        "latitude": getattr(c, "latitude", None),
        "longitude": getattr(c, "longitude", None),
    })


# ============================================================
#  PLACEHOLDERS ÉDITION / SUPPRESSION (NON UTILISÉS)
# ============================================================
def edit(request):
    return HttpResponse("edit - placeholder", content_type="text/plain; charset=utf-8")


def delete(request):
    return HttpResponse("delete - placeholder", content_type="text/plain; charset=utf-8")


# ============================================================
#  DÉTAIL COMMANDE
# ============================================================
def detail(request, order_id):
    # On charge la commande + client + partenaires + lignes + photos
    order = get_object_or_404(
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .prefetch_related("items__photos"),
        pk=order_id,
    )

    # Galerie globale : toutes les photos rattachées aux lignes de la commande
    all_photos = (
        OrderItemPhoto.objects
        .filter(order_item__order=order)
        .select_related("order_item")
    )

    context = {
        "order": order,
        "all_photos": all_photos,  # utilisé dans le bloc "Galerie globale"
        "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
    }
    return render(request, "orders/detail.html", context)


def order_ticket_pdf(request, order_id):
    """
    Ticket PDF premium FAGNI :
    - Logo + charte couleurs
    - Mise en page propre
    - QR code vers la fiche commande
    """
    order = get_object_or_404(
        Order.objects.select_related("customer", "laundry_partner", "delivery_partner")
                     .prefetch_related("items__service"),
        pk=order_id,
    )

    # ========= CONFIG DE BASE =========
    buffer = BytesIO()
    p = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    FAGNI_ORANGE = colors.HexColor("#ff7a00")
    FAGNI_BLUE = colors.HexColor("#0056b3")
    TEXT_DARK = colors.HexColor("#222222")
    GREY_SOFT = colors.HexColor("#666666")

    p.setTitle(f"Ticket FAGNI – {order.code or order.id}")

    # ========= PETITS HELPERS =========
    def draw_line_left(y, text, size=10, bold=False, color=TEXT_DARK):
        p.setFillColor(color)
        p.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        p.drawString(20 * mm, y, text)

    def draw_line_right(y, text, size=10, bold=False, color=TEXT_DARK):
        p.setFillColor(color)
        p.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        p.drawRightString(width - 20 * mm, y, text)

    def hline(y, color=colors.HexColor("#e5e7eb")):
        p.setStrokeColor(color)
        p.setLineWidth(0.5)
        p.line(20 * mm, y, width - 20 * mm, y)

    # ========= BANDEAU HAUT + LOGO + QR =========
    header_top = height - 15 * mm
    header_bottom = header_top - 22 * mm

    # Bandeau dégradé simplifié (2 rectangles superposés)
    p.setFillColor(FAGNI_ORANGE)
    p.rect(0, header_bottom, width, (header_top - header_bottom), stroke=0, fill=1)
    p.setFillColor(FAGNI_BLUE)
    p.rect(width * 0.45, header_bottom, width * 0.55, (header_top - header_bottom), stroke=0, fill=1)

    # Logo FAGNI (chemin par défaut : static/img/fagni_logo.png)
    logo_path = getattr(
        settings,
        "FAGNI_PDF_LOGO_PATH",
        os.path.join(settings.BASE_DIR, "static", "img", "fagni_logo.png"),
    )

    logo_height = 18 * mm
    logo_y = header_bottom + ((header_top - header_bottom) - logo_height) / 2

    try:
        if os.path.exists(logo_path):
            logo = ImageReader(logo_path)
            logo_w, logo_h = logo.getSize()
            ratio = logo_height / float(logo_h)
            logo_width = logo_w * ratio
            p.drawImage(
                logo,
                20 * mm,
                logo_y,
                width=logo_width,
                height=logo_height,
                mask="auto",
            )
    except Exception:
        # Si le logo pose problème, on laisse tomber, le ticket doit quand même sortir
        pass

    # Texte FAGNI à gauche
    p.setFillColor(colors.white)
    p.setFont("Helvetica-Bold", 14)
    p.drawString(20 * mm, header_top - 7 * mm, "FAGNI – Ticket commande")

    # Code + date dans le bandeau
    code_display = order.code or str(order.id)
    created_str = timezone.localtime(order.created_at).strftime("%d/%m/%Y %H:%M")

    p.setFont("Helvetica", 9)
    p.drawString(20 * mm, header_bottom + 4 * mm, f"Commande : {code_display}")
    p.drawRightString(width - 20 * mm, header_bottom + 4 * mm, f"Créée le {created_str}")

    # --- URL POUR LE QR-CODE ---
    # QR code à droite : URL publique vers le ticket A4
    ticket_url = _build_order_public_url(
        request,
        order,
        viewname="orders:order_ticket_pdf",  # 🔥 le QR pointe vers ce PDF
    )

    # --- Génération du QR code ---
    qr = qrcode.QRCode(
        version=1,
        box_size=6,
        border=2,
    )
    qr.add_data(ticket_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    qr_buffer = BytesIO()
    qr_img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    # --- DESSIN DU QR CODE SUR LE PDF (C'EST ÇA QUI MANQUAIT) ---
    try:
        qr_reader = ImageReader(qr_buffer)
        qr_size = 30 * mm
        qr_x = width - 20 * mm - qr_size
        qr_y = header_bottom + ((header_top - header_bottom) - qr_size) / 2
        p.drawImage(
            qr_reader,
            qr_x,
            qr_y,
            width=qr_size,
            height=qr_size,
            mask="auto",
        )
    except Exception:
        # Si le QR code plante, on ne bloque pas le ticket
        pass

    # ========= CONTENU =========
    y = header_bottom - 10 * mm

    # Statut
    status_label = dict(order.STATUS_CHOICES).get(order.status, order.status)
    draw_line_left(y, f"Statut : {status_label}", size=10, bold=True, color=FAGNI_BLUE)
    y -= 6 * mm
    hline(y)
    y -= 8 * mm

    # ------- Bloc client -------
    draw_line_left(y, "👤 Client", size=11, bold=True, color=FAGNI_ORANGE)
    y -= 6 * mm
    customer = order.customer

    draw_line_left(y, f"Nom : {customer.name}")
    y -= 5 * mm

    if customer.phone:
        draw_line_left(y, f"Tél : {customer.phone}")
        y -= 5 * mm

    if customer.address:
        draw_line_left(y, f"Adresse : {customer.address}")
        y -= 5 * mm

    if customer.latitude is not None and customer.longitude is not None:
        draw_line_left(y, f"GPS : {customer.latitude} / {customer.longitude}", size=9, color=GREY_SOFT)
        y -= 5 * mm

    y -= 4 * mm
    hline(y)
    y -= 8 * mm

    # ------- Bloc partenaires -------
    draw_line_left(y, "🧺 Partenaires", size=11, bold=True, color=FAGNI_ORANGE)
    y -= 6 * mm

    laundry_name = order.laundry_partner.name if order.laundry_partner else "Non assignée"
    delivery_name = order.delivery_partner.name if order.delivery_partner else "Non assigné"

    draw_line_left(y, f"Blanchisserie : {laundry_name}")
    y -= 5 * mm
    draw_line_left(y, f"Livreur : {delivery_name}")
    y -= 5 * mm

    if order.distance_km:
        draw_line_left(y, f"Distance A/R : {order.distance_km} km", size=9)
        y -= 4 * mm
        draw_line_left(
            y,
            f"Coût livreur : {order.driver_logistic_cost or 0} FCFA – Marge logistique : {order.logistic_margin or 0} FCFA",
            size=9,
            color=GREY_SOFT,
        )
        y -= 5 * mm

    y -= 4 * mm
    hline(y)
    y -= 8 * mm

    # ------- Détail des prestations -------
    draw_line_left(y, "📦 Détail des prestations", size=11, bold=True, color=FAGNI_ORANGE)
    y -= 7 * mm

    # En-tête colonnes
    p.setFont("Helvetica-Bold", 9)
    p.setFillColor(TEXT_DARK)
    p.drawString(20 * mm, y, "Désignation")
    p.drawRightString(width - 70 * mm, y, "Qté")
    p.drawRightString(width - 40 * mm, y, "PU")
    p.drawRightString(width - 20 * mm, y, "Total")
    y -= 4 * mm
    hline(y)
    y -= 6 * mm

    p.setFont("Helvetica", 9)
    p.setFillColor(TEXT_DARK)

    for item in order.items.all():
        if y < 40 * mm:
            p.showPage()
            width, height = A4
            y = height - 25 * mm
            draw_line_left(y, "Détail des prestations (suite)", size=11, bold=True, color=FAGNI_ORANGE)
            y -= 8 * mm
            p.setFont("Helvetica", 9)

        designation = item.designation
        if item.service and item.service.category:
            designation = f"{designation} ({item.service.category.name})"

        p.drawString(20 * mm, y, designation[:60])
        y -= 4 * mm

        qty = item.quantity
        pu = int(item.unit_price)
        tot = int(item.total)

        p.drawRightString(width - 70 * mm, y, str(qty))
        p.drawRightString(width - 40 * mm, y, f"{pu:,}".replace(",", " "))
        p.drawRightString(width - 20 * mm, y, f"{tot:,}".replace(",", " "))
        y -= 6 * mm

    y -= 4 * mm
    hline(y)
    y -= 8 * mm

    # ------- Totaux & synthèse financière -------
    total_ht = order.total_ht
    service_fee = order.service_fee or 0
    delivery_fee = order.delivery_fee or 0
    grand_total = order.grand_total

    box_height = 24 * mm
    box_y = y - box_height + 2 * mm

    p.setFillColor(colors.whitesmoke)
    p.roundRect(20 * mm, box_y, width - 40 * mm, box_height, 4 * mm, stroke=0, fill=1)

    y -= 4 * mm
    draw_line_left(y, "Total prestations (HT) :", size=10)
    draw_line_right(y, f"{int(total_ht):,} FCFA".replace(",", " "), size=10)
    y -= 5 * mm

    draw_line_left(y, "Service FAGNI :", size=10)
    draw_line_right(y, f"{int(service_fee):,} FCFA".replace(",", " "), size=10)
    y -= 5 * mm

    draw_line_left(y, "Frais de livraison :", size=10)
    draw_line_right(y, f"{int(delivery_fee):,} FCFA".replace(",", " "), size=10)
    y -= 7 * mm

    p.setFillColor(FAGNI_ORANGE)
    p.roundRect(20 * mm, box_y - 10 * mm, width - 40 * mm, 9 * mm, 4 * mm, stroke=0, fill=1)

    p.setFillColor(colors.white)
    p.setFont("Helvetica-Bold", 11)
    p.drawString(22 * mm, box_y - 7 * mm, "Total TTC à payer par le client")
    p.drawRightString(
        width - 22 * mm,
        box_y - 7 * mm,
        f"{int(grand_total):,} FCFA".replace(",", " "),
    )

    y = box_y - 16 * mm

    p.setFont("Helvetica-Oblique", 8)
    p.setFillColor(GREY_SOFT)
    draw_line_left(
        y,
        "Merci d’avoir utilisé FAGNI. Scanne le QR code pour retrouver le détail de la commande.",
        size=8,
        color=GREY_SOFT,
    )

    # ========= FIN =========
    p.showPage()
    p.save()

    pdf = buffer.getvalue()
    buffer.close()

    response = HttpResponse(content_type="application/pdf")
    filename = f"ticket_{order.code or order.id}.pdf"
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    response.write(pdf)
    return response


def _build_order_public_url(request, order, viewname="orders:detail"):
    """
    Construit une URL publique propre pour le QR code.

    - viewname : nom de l'URL Django (ex: 'orders:detail', 'orders:order_ticket_thermal_pdf')
    - Essaie d'abord reverse(viewname, order.id)
    - Utilise SITE_BASE_URL si défini (ex: http://192.168.1.6:8000)
    - Sinon, fallback sur request.build_absolute_uri(...)
    """
    try:
        relative = reverse(viewname, args=[order.id])
    except Exception:
        relative = "/"

    base = getattr(settings, "SITE_BASE_URL", "").strip()

    # Si pas défini ou 'null' → on utilise le host de la requête
    if not base or base.lower() == "null":
        return request.build_absolute_uri(relative)

    base = base.rstrip("/")
    return f"{base}{relative}"


def order_ticket_thermal_pdf(request, order_id):
    """
    Ticket PDF au format 'thermique' (80 mm) ultra propre :
    - logo FAGNI (si présent)
    - infos client
    - lignes de commande
    - totaux
    - QR code vers la page détail de la commande
    """
    order = get_object_or_404(
        Order.objects.select_related("customer", "laundry_partner", "delivery_partner")
                     .prefetch_related("items__service"),
        pk=order_id,
    )

    # ---------- Format ticket thermique ----------
    base_height = 260 + (len(order.items.all()) * 22)
    page_width = 226  # ~80 mm
    page_height = max(420, min(base_height, 900))
    margin_x = 10

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_width, page_height))

    y = page_height - 12

    title_color = (0, 0, 0)
    grey = 0.3
    light_grey = 0.7

    # ---------- Header : logo + nom FAGNI ----------
    logo_path = os.path.join(settings.BASE_DIR, "static", "img", "fagni_logo.png")
    has_logo = os.path.exists(logo_path)

    if has_logo:
        logo_width = 50
        logo_height = 28
        c.drawImage(
            logo_path,
            margin_x,
            y - logo_height,
            width=logo_width,
            height=logo_height,
            preserveAspectRatio=True,
            mask="auto",
        )
        c.setFont("Helvetica-Bold", 12)
        c.setFillColorRGB(*title_color)
        c.drawString(margin_x + logo_width + 6, y - 8, "FAGNI")
        c.setFont("Helvetica", 8)
        c.drawString(margin_x + logo_width + 6, y - 20, "Pressing & Services")
        y -= logo_height + 8
    else:
        c.setFont("Helvetica-Bold", 14)
        c.setFillColorRGB(*title_color)
        c.drawCentredString(page_width / 2, y, "FAGNI")
        y -= 18

    c.setStrokeColorRGB(light_grey, light_grey, light_grey)
    c.setLineWidth(0.5)
    c.line(margin_x, y, page_width - margin_x, y)
    y -= 8

    # ---------- Infos commande ----------
    c.setFont("Helvetica-Bold", 9)
    c.setFillColorRGB(*title_color)
    code_txt = f"Commande : {order.code or order.id}"
    c.drawString(margin_x, y, code_txt)
    y -= 12

    c.setFont("Helvetica", 8)
    created_txt = f"Créée le : {order.created_at.strftime('%d/%m/%Y %H:%M')}"
    c.drawString(margin_x, y, created_txt)
    y -= 10

    status_label = dict(order.STATUS_CHOICES).get(order.status, order.status)
    status_txt = f"Statut : {status_label}"
    c.drawString(margin_x, y, status_txt)
    y -= 14

    # ---------- Infos client ----------
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin_x, y, "Client")
    y -= 10

    c.setFont("Helvetica", 8)
    client_name = order.customer.name or ""
    c.drawString(margin_x, y, f"Nom : {client_name}")
    y -= 10

    if order.customer.phone:
        c.drawString(margin_x, y, f"Tél : {order.customer.phone}")
        y -= 10

    if order.customer.address:
        addr = order.customer.address
        max_len = 45
        if len(addr) > max_len:
            addr_line = addr[:max_len - 3] + "..."
        else:
            addr_line = addr
        c.drawString(margin_x, y, f"Adr : {addr_line}")
        y -= 10

    y -= 4
    c.setStrokeColorRGB(light_grey, light_grey, light_grey)
    c.line(margin_x, y, page_width - margin_x, y)
    y -= 8

    # ---------- Détail des articles ----------
    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin_x, y, "Articles")
    y -= 12

    c.setFont("Helvetica-Bold", 8)
    c.drawString(margin_x, y, "Libellé")
    c.drawRightString(page_width - margin_x - 72, y, "Qté")
    c.drawRightString(page_width - margin_x - 40, y, "PU")
    c.drawRightString(page_width - margin_x, y, "Total")
    y -= 8
    c.setStrokeColorRGB(light_grey, light_grey, light_grey)
    c.line(margin_x, y, page_width - margin_x, y)
    y -= 6

    c.setFont("Helvetica", 8)

    for item in order.items.all():
        if y < 80:
            c.showPage()
            y = page_height - 20
            c.setFont("Helvetica", 8)

        label = item.designation or ""
        max_len = 26
        if len(label) > max_len:
            label = label[:max_len - 3] + "..."

        c.drawString(margin_x, y, label)

        c.drawRightString(page_width - margin_x - 72, y, str(item.quantity))
        c.drawRightString(
            page_width - margin_x - 40,
            y,
            f"{int(item.unit_price):,}".replace(",", " "),
        )
        c.drawRightString(
            page_width - margin_x,
            y,
            f"{int(item.total):,}".replace(",", " "),
        )
        y -= 10

    y -= 4
    c.setStrokeColorRGB(light_grey, light_grey, light_grey)
    c.line(margin_x, y, page_width - margin_x, y)
    y -= 8

    # ---------- Totaux ----------
    total_ht = order.total_ht
    service_fee = order.service_fee or Decimal("0")
    delivery_fee = order.delivery_fee or Decimal("0")
    grand_total = order.grand_total

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(grey, grey, grey)
    c.drawString(margin_x, y, "Total prestation :")
    c.setFillColorRGB(0, 0, 0)
    c.drawRightString(
        page_width - margin_x,
        y,
        f"{int(total_ht):,} FCFA".replace(",", " "),
    )
    y -= 10

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(grey, grey, grey)
    c.drawString(margin_x, y, "Service FAGNI :")
    c.setFillColorRGB(0, 0, 0)
    c.drawRightString(
        page_width - margin_x,
        y,
        f"{int(service_fee):,} FCFA".replace(",", " "),
    )
    y -= 10

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(grey, grey, grey)
    c.drawString(margin_x, y, "Livraison :")
    c.setFillColorRGB(0, 0, 0)
    c.drawRightString(
        page_width - margin_x,
        y,
        f"{int(delivery_fee):,} FCFA".replace(",", " "),
    )
    y -= 12

    c.setStrokeColorRGB(light_grey, light_grey, light_grey)
    c.line(margin_x, y, page_width - margin_x, y)
    y -= 10

    c.setFont("Helvetica-Bold", 10)
    c.setFillColorRGB(0, 0, 0)
    c.drawString(margin_x, y, "Total à payer :")
    c.drawRightString(
        page_width - margin_x,
        y,
        f"{int(grand_total):,} FCFA".replace(",", " "),
    )
    y -= 18

    # --- URL POUR LE QR-CODE ---
    # --- URL POUR LE QR-CODE ---
    # Ici on pointe directement vers le ticket thermique
    ticket_url = _build_order_public_url(
        request,
        order,
        viewname="orders:order_ticket_thermal_pdf",  # 🔥 lien direct vers /ticket-thermal/
    )

    # --- Génération du QR code ---
    qr = qrcode.QRCode(
        version=1,
        box_size=6,
        border=2,
    )
    qr.add_data(ticket_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white")

    qr_buffer = BytesIO()
    qr_img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    # --- DESSIN DU QR CODE SUR LE TICKET ---
    try:
        qr_reader = ImageReader(qr_buffer)
        qr_size = 80  # pixels (~28–30 mm)
        qr_x = (page_width - qr_size) / 2
        qr_y = 40
        c.drawImage(
            qr_reader,
            qr_x,
            qr_y,
            width=qr_size,
            height=qr_size,
            mask="auto",
        )
    except Exception:
        pass

    # ---------- Footer ----------
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(grey, grey, grey)
    c.drawCentredString(
        page_width / 2,
        18,
        "Merci d'avoir utilisé FAGNI 🧺",
    )

    c.showPage()
    c.save()

    pdf = buffer.getvalue()
    buffer.close()

    filename = f"ticket_thermal_{order.code or order.id}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


def safe_decimal(value, default=Decimal("0")):
    try:
        if value in (None, ""):
            return default
        return Decimal(str(value))
    except Exception:
        return default


@login_required
def update(request, order_id):
    order = get_object_or_404(Order, pk=order_id)

    if request.method == "POST":

        # ---------- CLIENT ----------
        customer = order.customer
        customer.name = request.POST.get("client_name", "").strip()
        customer.phone = request.POST.get("client_phone", "").strip()
        customer.address = request.POST.get("client_address", "").strip()

        customer.latitude = safe_decimal(request.POST.get("client_lat"), None)
        customer.longitude = safe_decimal(request.POST.get("client_lng"), None)
        customer.save()

        # Code parrain si ton modèle Order a ce champ
        referral_code = request.POST.get("referral_code", "").strip()
        if hasattr(order, "referral_code"):
            order.referral_code = referral_code or getattr(order, "referral_code", "")

        # ---------- LIGNES ----------
        item_ids       = request.POST.getlist("item_id[]")
        service_ids    = request.POST.getlist("service_id[]")
        designations   = request.POST.getlist("designation[]")
        quantities     = request.POST.getlist("quantity[]")
        prices         = request.POST.getlist("unit_price[]")
        item_indexes   = request.POST.getlist("item_index[]")

        # On garde les lignes existantes qui restent dans le formulaire
        kept_ids = set()
        for iid in item_ids:
            if iid and iid.isdigit():
                kept_ids.add(int(iid))

        # Supprimer les lignes qui ne sont plus dans le formulaire
        if kept_ids:
            order.items.exclude(id__in=kept_ids).delete()
        else:
            order.items.all().delete()

        # Création / mise à jour des lignes
        for i, service_id in enumerate(service_ids):
            if not service_id:
                continue

            designation = designations[i] if i < len(designations) else ""
            q_raw = quantities[i] if i < len(quantities) else "1"
            p_raw = prices[i] if i < len(prices) else "0"
            idx_str = item_indexes[i] if i < len(item_indexes) else str(i)
            item_id = item_ids[i] if i < len(item_ids) else ""

            try:
                quantity = int(q_raw)
            except (TypeError, ValueError):
                quantity = 1

            price = safe_decimal(p_raw, Decimal("0"))

            if quantity <= 0 or not price or price <= 0:
                continue

            try:
                service = ServiceItem.objects.get(pk=service_id)
            except ServiceItem.DoesNotExist:
                service = None

            # Mise à jour ou création de la ligne
            if item_id and item_id.isdigit():
                order_item = OrderItem.objects.filter(order=order, pk=item_id).first()
                if order_item is None:
                    order_item = OrderItem(order=order)
            else:
                order_item = OrderItem(order=order)

            order_item.service = service
            order_item.designation = designation or (service.name if service else "Prestation")
            order_item.quantity = quantity
            order_item.unit_price = price
            order_item.save()

            # ---------- PHOTOS ----------
            file_field_name = f"photos_{idx_str}"
            files = request.FILES.getlist(file_field_name)
            for f in files:
                if f:
                    OrderItemPhoto.objects.create(order_item=order_item, image=f)

        # ---------- FRAIS LOGISTIQUES ----------
        if order.laundry_partner:
            order.delivery_fee = order.compute_delivery_fee()

        # ---------- RECALCUL CENTRAL ----------
        order.save()

        # --- GESTION DES PHOTOS PAR LIGNE (CREATE / UPDATE) ---
        #
        # On part du principe que :
        #  - le template envoie une ligne par item dans order.items.all
        #  - chaque input file est nommé photos_0, photos_1, etc.
        #  - dans detail.html et update.html on utilise item.photos.all
        #
        # Ici on RECRÉE les liens photo -> OrderItem pour ce POST.

        # On supprime éventuellement les liens existants si tu veux repartir propre
        # (si tu veux conserver toutes les anciennes photos, commente cette ligne)
        OrderItemPhoto.objects.filter(order_item__order=order).delete()

        # On récupère les items dans le même ordre que le template :
        items = list(order.items.all().order_by("id"))

        for idx, item in enumerate(items):
            field_name = f"photos_{idx}"          # photos_0, photos_1, ...
            files = request.FILES.getlist(field_name)
            if not files:
                continue

            for f in files:
                OrderItemPhoto.objects.create(
                    order_item=item,
                    image=f,
                )

        return redirect("orders:detail", order_id=order.id)

    # ---------- GET : AFFICHAGE ----------
    context = {
        "order": order,
        "service_categories": ServiceCategory.objects.all(),
        "service_items": ServiceItem.objects.filter(is_active=True),
        "delivery_min_fee": getattr(settings, "FAGNI_LOGISTICS", {}).get("client_min_fee", 0),
        "delivery_price_per_km": getattr(settings, "FAGNI_LOGISTICS", {}).get("client_price_per_km", 0),
        "delivery_fixed_fee": getattr(settings, "FAGNI_LOGISTICS", {}).get("client_fixed_fee", 0),
        "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
    }

    return render(request, "orders/update.html", context)


# ============================================================
#  TABLEAU DE BORD LIVREURS
# ============================================================
@login_required
def driver_dashboard(request):
    """
    Tableau de bord livreurs :
    - stats par livreur
    - filtres par période (date_from / date_to)
    - filtre min_orders (min commandes)
    - tri (marge, livraison, distance, nb commandes)
    """

    # --- Filtres GET ---
    date_from = request.GET.get("date_from") or ""
    date_to = request.GET.get("date_to") or ""
    min_orders = request.GET.get("min_orders") or ""
    sort = request.GET.get("sort") or "margin"

    base_qs = (
        Order.objects
        .select_related("delivery_partner")
        .filter(delivery_partner__isnull=False)
    )

    # Filtre sur dates de création
    if date_from:
        df = parse_date(date_from)
        if df:
            base_qs = base_qs.filter(created_at__date__gte=df)

    if date_to:
        dt = parse_date(date_to)
        if dt:
            base_qs = base_qs.filter(created_at__date__lte=dt)

    # 1) Stats brutes par livreur
    raw_stats = (
        base_qs
        .values("delivery_partner__id", "delivery_partner__name")
        .annotate(
            nb_orders=Count("id", distinct=True),
            total_delivery=Coalesce(Sum("delivery_fee"), Decimal("0.00")),
            total_driver_cost=Coalesce(Sum("driver_logistic_cost"), Decimal("0.00")),
            total_distance=Coalesce(Sum("distance_km"), Decimal("0.00")),
            photos_count=Coalesce(Count("items__photos", distinct=True), 0),
        )
    )

    # Filtre min_orders (en Python, plus simple)
    if min_orders:
        try:
            min_o = int(min_orders)
        except (ValueError, TypeError):
            min_o = 0
        if min_o > 0:
            raw_stats = [row for row in raw_stats if (row.get("nb_orders") or 0) >= min_o]
        else:
            raw_stats = list(raw_stats)
    else:
        raw_stats = list(raw_stats)

    driver_stats = []

    # 2) Totaux globaux (somme des lignes)
    global_nb_orders = 0
    global_total_delivery = Decimal("0.00")
    global_total_driver_cost = Decimal("0.00")
    global_total_distance = Decimal("0.00")
    global_total_photos = 0

    # 3) Calcul par livreur
    for row in raw_stats:
        delivery = row["total_delivery"] or Decimal("0.00")
        cost = row["total_driver_cost"] or Decimal("0.00")
        distance = row["total_distance"] or Decimal("0.00")
        photos = row["photos_count"] or 0
        nb_orders = row["nb_orders"] or 0

        margin = delivery - cost
        row["computed_margin"] = margin

        # Moyennes
        if nb_orders > 0:
            row["avg_distance_per_order"] = float(distance) / nb_orders if distance else 0.0
            row["avg_delivery_per_order"] = float(delivery) / nb_orders if delivery else 0.0
            row["avg_cost_per_order"] = float(cost) / nb_orders if cost else 0.0
        else:
            row["avg_distance_per_order"] = 0.0
            row["avg_delivery_per_order"] = 0.0
            row["avg_cost_per_order"] = 0.0

        # Taux de marge (en % de la livraison)
        if delivery > 0:
            row["margin_rate"] = float((margin / delivery) * 100)
        else:
            row["margin_rate"] = 0.0

        driver_stats.append(row)

        # Accumulation des totaux
        global_nb_orders += nb_orders
        global_total_delivery += delivery
        global_total_driver_cost += cost
        global_total_distance += distance
        global_total_photos += photos

    # 4) Tri en fonction du critère choisi
    if sort == "delivery":
        driver_stats.sort(key=lambda r: r.get("total_delivery") or Decimal("0.00"), reverse=True)
    elif sort == "distance":
        driver_stats.sort(key=lambda r: r.get("total_distance") or Decimal("0.00"), reverse=True)
    elif sort == "orders":
        driver_stats.sort(key=lambda r: r.get("nb_orders") or 0, reverse=True)
    else:  # "margin" par défaut
        driver_stats.sort(key=lambda r: r.get("computed_margin") or Decimal("0.00"), reverse=True)

    # 5) Identification du meilleur livreur en marge (pour badge)
    if driver_stats:
        best_margin = max(driver_stats, key=lambda r: r.get("computed_margin") or Decimal("0.00"))
        best_id = best_margin.get("delivery_partner__id")
        for r in driver_stats:
            r["is_best_margin"] = (r.get("delivery_partner__id") == best_id)
    else:
        for r in driver_stats:
            r["is_best_margin"] = False

    # 6) Marge globale = livraison - coût
    global_total_margin = global_total_delivery - global_total_driver_cost

    global_stats = {
        "nb_orders": global_nb_orders,
        "total_delivery": global_total_delivery,
        "total_driver_cost": global_total_driver_cost,
        "total_margin": global_total_margin,
        "total_distance": global_total_distance,
        "photos_count": global_total_photos,
    }

    context = {
        "driver_stats": driver_stats,
        "global_stats": global_stats,
        "date_from": date_from,
        "date_to": date_to,
        "min_orders": min_orders,
        "sort": sort,
    }
    return render(request, "orders/ops_drivers.html", context)


# ============================================================
#  TABLEAU DE BORD FINANCIER
# ============================================================
@login_required
def finance_dashboard(request):
    """
    Dashboard financier FAGNI :
    - filtres par période (date_from / date_to)
    - filtre par statut financier (toutes / soldées / partiellement payées / non payées)
    - filtre par montant minimum (total global client)
    - synthèse globale + listing des dernières commandes (max 500)
    """

    # --- Filtres GET ---
    date_from = request.GET.get("date_from") or ""
    date_to = request.GET.get("date_to") or ""
    status_filter = request.GET.get("status") or "all"  # all / paid / partial / unpaid
    min_amount_input = request.GET.get("min_amount") or ""

    qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
    )

    # Filtre période sur la date de création
    if date_from:
        df = parse_date(date_from)
        if df:
            qs = qs.filter(created_at__date__gte=df)

    if date_to:
        dt = parse_date(date_to)
        if dt:
            qs = qs.filter(created_at__date__lte=dt)

    qs = qs.order_by("-created_at")

    # On limite à 500 commandes pour garder un dashboard rapide
    raw_orders = list(qs[:500])

    # Petit helper pour convertir en Decimal proprement
    def d(val):
        if isinstance(val, Decimal):
            return val
        if val in (None, "", 0):
            return Decimal("0")
        try:
            return Decimal(str(val))
        except Exception:
            return Decimal("0")

    # Enrichissement des commandes avec les montants calculés
    enriched_orders = []
    for o in raw_orders:
        base = d(getattr(o, "total", None))  # montant prestations
        service = d(getattr(o, "service_fee", None))
        delivery = d(getattr(o, "delivery_fee", None))
        logi_margin = d(getattr(o, "logistic_margin", None))

        paid = d(getattr(o, "amount_paid", None))
        due = d(getattr(o, "amount_due", None))

        # Montants calculés par commande
        o.base_total = base
        o.total_global_client = base + service + delivery
        o.margin_fagni = service + logi_margin
        o.paid = paid
        o.due = due
        o.is_fully_paid = (due <= 0)

        enriched_orders.append(o)

    # Conversion du filtre montant minimum
    try:
        min_amount = Decimal(min_amount_input) if min_amount_input else Decimal("0")
    except Exception:
        min_amount = Decimal("0")

    # Application des filtres "financiers" en Python
    filtered_orders = []
    for o in enriched_orders:
        # Filtre montant global client
        if min_amount > 0 and o.total_global_client < min_amount:
            continue

        # Filtre statut financier
        if status_filter == "paid":
            if not o.is_fully_paid:
                continue
        elif status_filter == "partial":
            if not (o.paid > 0 and o.due > 0):
                continue
        elif status_filter == "unpaid":
            if not (o.paid == 0 and o.due > 0):
                continue

        filtered_orders.append(o)

    # Totaux globaux calculés sur l'ensemble des commandes FILTRÉES
    total_orders = len(filtered_orders)
    total_prestations = Decimal("0")
    total_service = Decimal("0")
    total_delivery = Decimal("0")
    total_logistic_margin = Decimal("0")
    total_paid = Decimal("0")
    total_due = Decimal("0")

    for o in filtered_orders:
        total_prestations += d(o.base_total)
        total_service += d(o.service_fee)
        total_delivery += d(o.delivery_fee)
        total_logistic_margin += d(getattr(o, "logistic_margin", None))
        total_paid += d(o.paid)
        total_due += d(o.due)

    total_margin_fagni = total_service + total_logistic_margin

    context = {
        "orders": filtered_orders,
        "total_orders": total_orders,
        "total_prestations": total_prestations,
        "total_service": total_service,
        "total_delivery": total_delivery,
        "total_logistic_margin": total_logistic_margin,
        "total_margin_fagni": total_margin_fagni,
        "total_paid": total_paid,
        "total_due": total_due,
        # filtres pour le template
        "date_from": date_from,
        "date_to": date_to,
        "status_filter": status_filter,
        "min_amount": min_amount_input,
    }
    return render(request, "orders/finance_dashboard.html", context)


@login_required
def export_finance_xlsx(request):
    """
    Export Excel du dashboard financier FAGNI :
    - Reprend les mêmes filtres que finance_dashboard
    - Onglet 1 : Synthèse
    - Onglet 2 : Détail des commandes
    """

    date_from = request.GET.get("date_from") or ""
    date_to = request.GET.get("date_to") or ""
    status_filter = request.GET.get("status") or "all"
    min_amount_input = request.GET.get("min_amount") or ""

    qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
    )

    if date_from:
        df = parse_date(date_from)
        if df:
            qs = qs.filter(created_at__date__gte=df)

    if date_to:
        dt = parse_date(date_to)
        if dt:
            qs = qs.filter(created_at__date__lte=dt)

    qs = qs.order_by("-created_at")
    raw_orders = list(qs[:500])

    def d(val):
        if isinstance(val, Decimal):
            return val
        if val in (None, "", 0):
            return Decimal("0")
        try:
            return Decimal(str(val))
        except Exception:
            return Decimal("0")

    enriched_orders = []
    for o in raw_orders:
        base = d(getattr(o, "total", None))
        service = d(getattr(o, "service_fee", None))
        delivery = d(getattr(o, "delivery_fee", None))
        logi_margin = d(getattr(o, "logistic_margin", None))

        paid = d(getattr(o, "amount_paid", None))
        due = d(getattr(o, "amount_due", None))

        o.base_total = base
        o.total_global_client = base + service + delivery
        o.margin_fagni = service + logi_margin
        o.paid = paid
        o.due = due
        o.is_fully_paid = (due <= 0)

        enriched_orders.append(o)

    try:
        min_amount = Decimal(min_amount_input) if min_amount_input else Decimal("0")
    except Exception:
        min_amount = Decimal("0")

    filtered_orders = []
    for o in enriched_orders:
        if min_amount > 0 and o.total_global_client < min_amount:
            continue

        if status_filter == "paid":
            if not o.is_fully_paid:
                continue
        elif status_filter == "partial":
            if not (o.paid > 0 and o.due > 0):
                continue
        elif status_filter == "unpaid":
            if not (o.paid == 0 and o.due > 0):
                continue

        filtered_orders.append(o)

    total_orders = len(filtered_orders)
    total_prestations = Decimal("0")
    total_service = Decimal("0")
    total_delivery = Decimal("0")
    total_logistic_margin = Decimal("0")
    total_paid = Decimal("0")
    total_due = Decimal("0")

    for o in filtered_orders:
        total_prestations += d(o.base_total)
        total_service += d(o.service_fee)
        total_delivery += d(o.delivery_fee)
        total_logistic_margin += d(getattr(o, "logistic_margin", None))
        total_paid += d(o.paid)
        total_due += d(o.due)

    total_margin_fagni = total_service + total_logistic_margin

    wb = Workbook()

    title_font = Font(size=16, bold=True, color="FFFFFF")
    header_font = Font(bold=True, color="FFFFFF")
    label_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="0056B3")
    section_fill = PatternFill("solid", fgColor="FF7A00")
    thin_border = Border(
        left=Side(style="thin", color="DDDDDD"),
        right=Side(style="thin", color="DDDDDD"),
        top=Side(style="thin", color="DDDDDD"),
        bottom=Side(style="thin", color="DDDDDD"),
    )
    center = Alignment(horizontal="center", vertical="center")
    right = Alignment(horizontal="right", vertical="center")
    left = Alignment(horizontal="left", vertical="center")
    wrap = Alignment(wrap_text=True, vertical="top")

    ws1 = wb.active
    ws1.title = "Synthèse"

    ws1.merge_cells("A1:D1")
    cell_title = ws1["A1"]
    cell_title.value = "FAGNI – Dashboard financier (export)"
    cell_title.font = title_font
    cell_title.fill = header_fill
    cell_title.alignment = center

    ws1["A3"] = "Généré le"
    ws1["A3"].font = label_font
    ws1["B3"] = timezone.localtime().strftime("%d/%m/%Y %H:%M")

    ws1["A4"] = "Période"
    ws1["A4"].font = label_font
    if date_from or date_to:
        txt_period = ""
        if date_from:
            txt_period += f"du {date_from} "
        if date_to:
            txt_period += f"au {date_to}"
    else:
        txt_period = "Toutes les dates"
    ws1["B4"] = txt_period

    ws1["A5"] = "Filtre statut financier"
    ws1["A5"].font = label_font
    if status_filter == "paid":
        ws1["B5"] = "Soldées"
    elif status_filter == "partial":
        ws1["B5"] = "Partiellement payées"
    elif status_filter == "unpaid":
        ws1["B5"] = "Non payées"
    else:
        ws1["B5"] = "Toutes"

    ws1["A6"] = "Montant min. (total client)"
    ws1["A6"].font = label_font
    ws1["B6"] = f"{min_amount_input or '0'} FCFA"

    ws1.merge_cells("A8:D8")
    cell_sec = ws1["A8"]
    cell_sec.value = "Synthèse des montants (après filtres)"
    cell_sec.font = Font(bold=True, color="FFFFFF")
    cell_sec.fill = section_fill
    cell_sec.alignment = left

    rows_totaux = [
        ("Nombre de commandes", total_orders),
        ("Total prestations (TTC)", f"{total_prestations} FCFA"),
        ("Service FAGNI (global)", f"{total_service} FCFA"),
        ("Livraison facturée", f"{total_delivery} FCFA"),
        ("Marge logistique", f"{total_logistic_margin} FCFA"),
        ("Marge FAGNI (Service + Logistique)", f"{total_margin_fagni} FCFA"),
        ("Montant encaissé", f"{total_paid} FCFA"),
        ("Montant dû", f"{total_due} FCFA"),
    ]

    start_row = 10
    for idx, (label, value) in enumerate(rows_totaux):
        r = start_row + idx
        ws1[f"A{r}"] = label
        ws1[f"A{r}"].font = label_font
        ws1[f"B{r}"] = value

    ws1.column_dimensions["A"].width = 40
    ws1.column_dimensions["B"].width = 35

    ws2 = wb.create_sheet(title="Commandes")

    headers = [
        "Code",
        "Date création",
        "Statut commande",
        "Client",
        "Téléphone",
        "Adresse",
        "Total prestations TTC",
        "Service FAGNI",
        "Livraison",
        "Total global client",
        "Montant payé",
        "Montant dû",
        "Statut financier",
        "Marge FAGNI",
        "Blanchisserie",
        "Livreur",
        "Distance AR (km)",
        "Marge logistique",
    ]

    for col_idx, head in enumerate(headers, start=1):
        cell = ws2.cell(row=1, column=col_idx, value=head)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
        cell.border = thin_border

    row_idx = 2
    for o in filtered_orders:
        if o.is_fully_paid:
            f_status = "Soldée"
        elif o.due > 0 and o.paid > 0:
            f_status = "Partiellement payée"
        elif o.due > 0:
            f_status = "Non payée"
        else:
            f_status = ""

        row_vals = [
            o.code or o.id,
            o.created_at.strftime("%d/%m/%Y %H:%M") if o.created_at else "",
            o.get_status_display(),
            o.customer.name if o.customer else "",
            o.customer.phone if o.customer else "",
            o.customer.address if o.customer else "",
            float(o.base_total),
            float(d(o.service_fee)),
            float(d(o.delivery_fee)),
            float(o.total_global_client),
            float(o.paid),
            float(o.due),
            f_status,
            float(o.margin_fagni),
            o.laundry_partner.name if o.laundry_partner else "",
            o.delivery_partner.name if o.delivery_partner else "",
            float(d(getattr(o, "distance_km", None))),
            float(d(getattr(o, "logistic_margin", None))),
        ]

        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws2.cell(row=row_idx, column=col_idx, value=val)
            cell.border = thin_border
            if col_idx in (7, 8, 9, 10, 11, 12, 14, 18):
                cell.alignment = right
                cell.number_format = "#,##0"
            elif col_idx == 17:
                cell.alignment = right
                cell.number_format = "0.0"
            elif col_idx in (4, 5, 6, 15, 16, 13):
                cell.alignment = wrap
            else:
                cell.alignment = left

        row_idx += 1

    widths = [14, 18, 16, 20, 16, 30, 16, 14, 14, 18, 14, 14, 18, 16, 20, 20, 14, 16]
    for col_idx, w in enumerate(widths, start=1):
        ws2.column_dimensions[chr(64 + col_idx)].width = w

    ws2.auto_filter.ref = ws2.dimensions

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    filename = f"fagni_finance_{timezone.now().strftime('%Y%m%d_%H%M')}.xlsx"
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


# ============================================================
#  TOP CLIENTS CSV
# ============================================================
def export_top_clients_csv(request):
    """
    Export CSV des 100 meilleurs clients par montant cumulé.
    On agrège sur Order.total et, en fallback, sur quantity * unit_price.
    """
    items_total = Sum(
        ExpressionWrapper(
            F("items__quantity") * F("items__unit_price"),
            output_field=DEC,
        ),
        output_field=DEC,
    )

    qs = (
        Order.objects
        .select_related("customer")
        .values("customer__name")
        .annotate(
            nb_cmd=Count("id", distinct=True),
            montant_total=Coalesce(
                Sum("total"),
                items_total,
                output_field=DEC,
            ),
        )
        .order_by("-montant_total")[:100]
    )

    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="top_clients.csv"'
    resp.write("Client,Nb Commandes,Montant Total\n")
    for row in qs:
        name = (
            (row.get("customer__name") or "")
            .replace('"', "")
            .replace("\n", " ")
            .replace("\r", " ")
        )
        nb = int(row.get("nb_cmd") or 0)
        total = row.get("montant_total") or 0
        resp.write(f"{name},{nb},{total}\n")
    return resp


def export_top_clients_xlsx(request):
    """
    Export Excel des 100 meilleurs clients par montant cumulé.
    Même logique que export_top_clients_csv mais avec un design Excel.
    """
    items_total = Sum(
        ExpressionWrapper(
            F("items__quantity") * F("items__unit_price"),
            output_field=DEC,
        ),
        output_field=DEC,
    )

    qs = (
        Order.objects
        .select_related("customer")
        .values("customer__name")
        .annotate(
            nb_cmd=Count("id", distinct=True),
            montant_total=Coalesce(
                Sum("total"),
                items_total,
                output_field=DEC,
            ),
        )
        .order_by("-montant_total")[:100]
    )

    wb = Workbook()
    title_font = Font(size=16, bold=True, color="FFFFFF")
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="0056B3")
    thin_border = Border(
        left=Side(style="thin", color="DDDDDD"),
        right=Side(style="thin", color="DDDDDD"),
        top=Side(style="thin", color="DDDDDD"),
        bottom=Side(style="thin", color="DDDDDD"),
    )
    center = Alignment(horizontal="center", vertical="center")
    right = Alignment(horizontal="right", vertical="center")
    left = Alignment(horizontal="left", vertical="center")

    ws = wb.active
    ws.title = "Top clients"

    ws.merge_cells("A1:D1")
    t = ws["A1"]
    t.value = "FAGNI – Top 100 clients"
    t.font = title_font
    t.fill = header_fill
    t.alignment = center

    ws["A3"] = "Généré le"
    ws["B3"] = timezone.localtime().strftime("%d/%m/%Y %H:%M")

    headers = ["Rang", "Client", "Nb commandes", "Montant total (FCFA)"]
    for col_idx, head in enumerate(headers, start=1):
        c = ws.cell(row=5, column=col_idx, value=head)
        c.font = header_font
        c.fill = header_fill
        c.alignment = center
        c.border = thin_border

    row_idx = 6
    rank = 1
    for row in qs:
        name = (
            (row.get("customer__name") or "")
            .replace('"', "")
            .replace("\n", " ")
            .replace("\r", " ")
        )
        nb = int(row.get("nb_cmd") or 0)
        total = row.get("montant_total") or 0

        vals = [rank, name, nb, float(total)]
        for col_idx, val in enumerate(vals, start=1):
            c = ws.cell(row=row_idx, column=col_idx, value=val)
            c.border = thin_border
            if col_idx in (1, 3, 4):
                c.alignment = right
                if col_idx == 4:
                    c.number_format = "#,##0"
            else:
                c.alignment = left

        rank += 1
        row_idx += 1

    widths = [8, 32, 14, 20]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    ws.auto_filter.ref = f"A5:D{row_idx - 1}"

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    resp = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = 'attachment; filename="fagni_top_clients.xlsx"'
    return resp


# ============================================================
#  CHANGEMENT DE STATUT SIMPLE
# ============================================================
@require_POST
def change_status(request, order_id):
    order = get_object_or_404(Order, pk=order_id)

    new_status = request.POST.get("status")

    valid_statuses = dict(Order.STATUS_CHOICES).keys()
    if new_status in valid_statuses:
        order.status = new_status
        order.save()

    return redirect("orders:detail", order_id=order.id)


def _safe_dec(val):
    if isinstance(val, Decimal):
        return val
    if val in (None, "", 0):
        return DECIMAL_ZERO
    try:
        return Decimal(str(val))
    except Exception:
        return DECIMAL_ZERO


