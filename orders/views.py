import csv
import json
from decimal import Decimal, InvalidOperation
from datetime import timedelta
from django.template.loader import render_to_string
from django.db import models
from django.contrib import messages
from weasyprint import HTML
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
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
    Avg,
    DurationField,
)
from django.db import transaction
from django.db.models.functions import Coalesce, Cast
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.utils.dateparse import parse_date
from django.utils.encoding import smart_str
from .models import (
    Order,
    Customer,
    OrderItem,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
    DeliveryLeg,
    OrderStatusHistory,
)
from .utils import auto_assign_laundry, auto_assign_delivery
from partners.models import LaundryPartner, DeliveryPartner
from mlm.services import attach_customer_to_sponsor

from io import BytesIO
import os
import io
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4, A6, mm
from reportlab.lib.units import mm

from reportlab.lib import colors
from reportlab.lib.utils import ImageReader
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
from collections import Counter, defaultdict
import qrcode
from bonuses.models import BonusWeek

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
#  LISTE CLIENTS – MINI CRM
# ============================================================
def customers_list(request):
    """
    Liste des clients FAGNI (mini CRM) :
    - Recherche (nom, téléphone, adresse)
    - Filtre min_orders (nb min de commandes)
    - Stats : nb commandes, montant total, service FAGNI, livraison
    Basé directement sur la relation Customer -> orders.
    """

    q = (request.GET.get("q") or "").strip()
    min_orders = request.GET.get("min_orders") or ""

    # 1) Base : tous les clients avec agrégats sur la relation "orders"
    qs = (
        Customer.objects
        .annotate(
            total_orders=Count("orders", distinct=True),
            total_amount=Coalesce(Sum("orders__total"), DECIMAL_ZERO),
            total_service_fee=Coalesce(Sum("orders__service_fee"), DECIMAL_ZERO),
            total_delivery_fee=Coalesce(Sum("orders__delivery_fee"), DECIMAL_ZERO),
            last_order_date=Max("orders__created_at"),
        )
    )

    # 2) Recherche plein texte
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(address__icontains=q)
        )

    # 3) Filtre min_orders
    if min_orders:
        try:
            min_o = int(min_orders)
        except (TypeError, ValueError):
            min_o = 0
        if min_o > 0:
            qs = qs.filter(total_orders__gte=min_o)

    # 4) Tri : dernière commande d'abord, puis nom
    qs = qs.order_by(
        F("last_order_date").desc(nulls_last=True),
        "name",
    )

    # 5) Totaux de synthèse globaux (pour la barre en haut)
    total_customers = qs.count()
    total_with_orders = qs.filter(total_orders__gt=0).count()

    agg = qs.aggregate(
        total_amount_global=Coalesce(Sum("total_amount"), DECIMAL_ZERO),
        total_service_global=Coalesce(Sum("total_service_fee"), DECIMAL_ZERO),
        total_delivery_global=Coalesce(Sum("total_delivery_fee"), DECIMAL_ZERO),
    )

    context = {
        "customers": qs,
        "total_customers": total_customers,
        "total_with_orders": total_with_orders,
        "q": q,
        "min_orders": min_orders,
        # synthèse globale
        "total_amount": agg["total_amount_global"],
        "total_service": agg["total_service_global"],
        "total_delivery": agg["total_delivery_global"],
    }
    return render(request, "orders/customers_list.html", context)


@login_required
def finance_dashboard(request):
    """
    Dashboard financier FAGNI :
    - Filtres : date_from, date_to, status (paid/partial/unpaid/all), min_amount
    - Totaux calculés EN PYTHON pour éviter les problèmes de types
    """

    date_from = request.GET.get("date_from") or ""
    date_to = request.GET.get("date_to") or ""
    status_filter = request.GET.get("status") or "all"   # paid / partial / unpaid / all
    min_amount_input = request.GET.get("min_amount") or ""

    qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .order_by("-created_at")
    )

    # --- Période ---
    if date_from:
        df = parse_date(date_from)
        if df:
            qs = qs.filter(created_at__date__gte=df)

    if date_to:
        dt = parse_date(date_to)
        if dt:
            qs = qs.filter(created_at__date__lte=dt)

    # On limite le nombre de lignes affichées (sécurité)
    raw_orders = list(qs[:500])

    # Helper décimal local
    def d(val):
        return _safe_dec(val)

    enriched_orders = []
    for o in raw_orders:
        # Montant "prestations" (total / grand_total / total_ttc / total_ht)
        base = _order_effective_total(o)

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
        o.is_fully_paid = (due <= DECIMAL_ZERO)

        enriched_orders.append(o)

    # --- Filtre montant minimum ---
    try:
        min_amount = Decimal(min_amount_input) if min_amount_input else DECIMAL_ZERO
    except Exception:
        min_amount = DECIMAL_ZERO

    filtered_orders = []
    for o in enriched_orders:
        if min_amount > DECIMAL_ZERO and o.total_global_client < min_amount:
            continue

        if status_filter == "paid":
            # Soldées
            if not o.is_fully_paid:
                continue
        elif status_filter == "partial":
            # Partiellement payées
            if not (o.paid > DECIMAL_ZERO and o.due > DECIMAL_ZERO):
                continue
        elif status_filter == "unpaid":
            # Non payées
            if not (o.paid == DECIMAL_ZERO and o.due > DECIMAL_ZERO):
                continue

        filtered_orders.append(o)

    # --- Totaux globaux ---
    total_orders = len(filtered_orders)

    total_prestations = DECIMAL_ZERO
    total_service = DECIMAL_ZERO
    total_delivery = DECIMAL_ZERO
    total_logistic_margin = DECIMAL_ZERO
    total_paid = DECIMAL_ZERO
    total_due = DECIMAL_ZERO

    count_paid = 0
    count_partial = 0
    count_unpaid = 0

    for o in filtered_orders:
        total_prestations += d(o.base_total)
        total_service += d(getattr(o, "service_fee", None))
        total_delivery += d(getattr(o, "delivery_fee", None))
        total_logistic_margin += d(getattr(o, "logistic_margin", None))
        total_paid += d(o.paid)
        total_due += d(o.due)

        if o.is_fully_paid:
            count_paid += 1
        elif o.paid > DECIMAL_ZERO and o.due > DECIMAL_ZERO:
            count_partial += 1
        elif o.paid == DECIMAL_ZERO and o.due > DECIMAL_ZERO:
            count_unpaid += 1

    total_margin_fagni = total_service + total_logistic_margin

    context = {
        "orders": filtered_orders,

        "date_from": date_from,
        "date_to": date_to,
        "status_filter": status_filter,
        "min_amount": min_amount_input,

        "total_orders": total_orders,
        "total_prestations": total_prestations,
        "total_service": total_service,
        "total_delivery": total_delivery,
        "total_logistic_margin": total_logistic_margin,
        "total_margin_fagni": total_margin_fagni,
        "total_paid": total_paid,
        "total_due": total_due,

        "count_paid": count_paid,
        "count_partial": count_partial,
        "count_unpaid": count_unpaid,
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
    status_filter = request.GET.get("status") or "all"   # paid / partial / unpaid / all
    min_amount_input = request.GET.get("min_amount") or ""

    qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .order_by("-created_at")
    )

    # Période
    if date_from:
        df = parse_date(date_from)
        if df:
            qs = qs.filter(created_at__date__gte=df)

    if date_to:
        dt = parse_date(date_to)
        if dt:
            qs = qs.filter(created_at__date__lte=dt)

    # On limite pour éviter un fichier énorme
    raw_orders = list(qs[:500])

    def d(val):
        """Helper décimal safe."""
        if isinstance(val, Decimal):
            return val
        if val in (None, "", 0):
            return Decimal("0")
        try:
            return Decimal(str(val))
        except Exception:
            return Decimal("0")

    # Enrichissement Python (on évite ici les agrégations mixtes)
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

    # Filtre montant mini
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

    # Totaux
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

    # ---------- Excel ----------
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

    # --- Onglet 1 : Synthèse ---
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

    # --- Onglet 2 : Commandes ---
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
            elif col_idx in (4, 5, 6, 13, 15, 16):
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
    Basé directement sur la relation Customer -> orders.
    """

    q = (request.GET.get("q") or "").strip()
    min_orders = request.GET.get("min_orders") or ""

    # 1) Base : tous les clients avec agrégats sur la relation "orders"
    qs = (
        Customer.objects
        .annotate(
            total_orders=Count("orders", distinct=True),
            total_amount=Coalesce(Sum("orders__total"), DECIMAL_ZERO),
            total_service_fee=Coalesce(Sum("orders__service_fee"), DECIMAL_ZERO),
            total_delivery_fee=Coalesce(Sum("orders__delivery_fee"), DECIMAL_ZERO),
            last_order_date=Max("orders__created_at"),
        )
    )

    # 2) Recherche plein texte
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(phone__icontains=q)
            | Q(address__icontains=q)
        )

    # 3) Filtre min_orders
    if min_orders:
        try:
            min_o = int(min_orders)
        except (TypeError, ValueError):
            min_o = 0
        if min_o > 0:
            qs = qs.filter(total_orders__gte=min_o)

    # 4) Tri : dernière commande d'abord, puis nom
    qs = qs.order_by(
        F("last_order_date").desc(nulls_last=True),
        "name",
    )

    # 5) Totaux de synthèse globaux (pour la barre en haut)
    total_customers = qs.count()
    total_with_orders = qs.filter(total_orders__gt=0).count()

    agg = qs.aggregate(
        total_amount_global=Coalesce(Sum("total_amount"), DECIMAL_ZERO),
        total_service_global=Coalesce(Sum("total_service_fee"), DECIMAL_ZERO),
        total_delivery_global=Coalesce(Sum("total_delivery_fee"), DECIMAL_ZERO),
    )

    context = {
        "customers": qs,
        "total_customers": total_customers,
        "total_with_orders": total_with_orders,
        "q": q,
        "min_orders": min_orders,
        # synthèse globale
        "total_amount": agg["total_amount_global"],
        "total_service": agg["total_service_global"],
        "total_delivery": agg["total_delivery_global"],
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
        # Il peut déjà exister PLUSIEURS clients avec le même téléphone,
        # donc on protège le get_or_create contre MultipleObjectsReturned.
        try:
            customer, created = Customer.objects.get_or_create(
                phone=phone,
                defaults={
                    "name": name,
                    "address": address,
                },
            )
        except Customer.MultipleObjectsReturned:
            # On prend le plus récent avec ce téléphone
            customer = (
                Customer.objects.filter(phone=phone)
                .order_by("-id")
                .first()
            )
            created = False

            # On met éventuellement à jour nom / adresse si on a mieux
            changed = False
            if name and customer.name != name:
                customer.name = name
                changed = True
            if address and customer.address != address:
                customer.address = address
                changed = True

            if changed:
                customer.save()
        else:
            # Cas normal : 0 ou 1 client trouvé
            # On peut aussi rafraîchir les infos si elles ont changé
            updated = False
            if name and customer.name != name:
                customer.name = name
                updated = True
            if address and customer.address != address:
                customer.address = address
                updated = True

            if updated:
                customer.save()

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

        order.notes = request.POST.get("order_notes", "").strip()

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
    """
    Détail d’une commande FAGNI :
    - infos client + adresse + notes
    - timeline statuts (lecture)
    - lignes + photos
    - totaux
    - formulaire de changement de statut (POST vers update_status)
    """
    order = get_object_or_404(
        Order.objects.select_related("customer", "laundry_partner", "delivery_partner"),
        pk=order_id,
    )

    # Lignes préchargées avec les services & photos
    items = (
        order.items.all()
        .select_related("service")
        .prefetch_related("photos")
        .order_by("id")
    )

    # Choices de statut pour le <select> sur la page
    status_choices = getattr(Order, "STATUS_CHOICES", getattr(Order, "STATUS", []))

    context = {
        "order": order,
        "items": items,
        "status_choices": status_choices,
    }
    return render(request, "orders/detail.html", context)


@require_POST
def update_status(request, order_id):
    order = get_object_or_404(Order, pk=order_id)

    new_status = request.POST.get("status")
    valid_status = dict(Order.STATUS_CHOICES).keys()

    if new_status not in valid_status:
        messages.error(request, "Statut invalide.")
        return redirect("orders:detail", order_id=order.id)

    old_status = order.status
    if new_status == old_status:
        messages.info(request, "Le statut est déjà à cette valeur.")
        return redirect("orders:detail", order_id=order.id)

    order.status = new_status
    order.save(update_fields=["status", "updated_at"])

    OrderStatusHistory.objects.create(
        order=order,
        previous_status=old_status,
        new_status=new_status,
        changed_by=request.user if request.user.is_authenticated else None,
    )

    messages.success(request, "Statut de la commande mis à jour.")
    return redirect("orders:detail", order_id=order.id)


def _build_order_public_url(request, order, viewname="orders:detail"):
    """
    Construit une URL absolue vers une vue donnée pour une commande.
    """
    relative = reverse(viewname, args=[order.id])
    return request.build_absolute_uri(relative)


def order_ticket_thermal_pdf(request, order_id):
    """
    Ticket PDF au format 'thermique' (80 mm) :

    - logo FAGNI (si présent)
    - infos client
    - partenaires (blanchisserie / livreur)
    - lignes de commande
    - totaux (total prestations, service, livraison, total global, payé, dû)
    - QR code vers le ticket thermique lui-même
    """
    order = get_object_or_404(
        Order.objects
             .select_related("customer", "laundry_partner", "delivery_partner")
             .prefetch_related("items__service"),
        pk=order_id,
    )

    items = list(order.items.all())

    # ---------- Format ticket thermique ----------
    base_height = 260 + (len(items) * 22)
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

    # Ligne de séparation
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

    # Code parrain : on regarde d'abord sur la commande si le champ existe,
    # sinon on essaye de le prendre sur le client.
    referral_code = getattr(order, "referral_code", None)
    if not referral_code and hasattr(order.customer, "referral_code"):
        referral_code = order.customer.referral_code

    if referral_code:
        c.drawString(margin_x, y, f"Code parrain : {referral_code}")
        y -= 10

    if getattr(order, "distance_km", None):
        c.drawString(margin_x, y, f"Distance A/R : {order.distance_km} km")
        y -= 10

    # ---------- Partenaires ----------
    y -= 4
    c.setStrokeColorRGB(light_grey, light_grey, light_grey)
    c.line(margin_x, y, page_width - margin_x, y)
    y -= 8

    c.setFont("Helvetica-Bold", 9)
    c.drawString(margin_x, y, "Partenaires")
    y -= 10

    c.setFont("Helvetica", 8)
    laundry_name = order.laundry_partner.name if order.laundry_partner else "Non assignée"
    delivery_name = order.delivery_partner.name if order.delivery_partner else "Non assigné"
    c.drawString(margin_x, y, f"Blanchisserie : {laundry_name}")
    y -= 10
    c.drawString(margin_x, y, f"Livreur     : {delivery_name}")
    y -= 12

    # ---------- Notes éventuelles ----------
    if order.notes:
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin_x, y, "Notes / instructions")
        y -= 10
        c.setFont("Helvetica", 8)
        # on tronque si très long
        notes = order.notes.replace("\r\n", " ").replace("\n", " ")
        max_len = 90
        if len(notes) > max_len:
            notes_line = notes[:max_len - 3] + "..."
        else:
            notes_line = notes
        c.drawString(margin_x, y, notes_line)
        y -= 12

    # Ligne de séparation avant articles
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
    for item in items:
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

    # Ligne avant totaux
    y -= 4
    c.setStrokeColorRGB(light_grey, light_grey, light_grey)
    c.line(margin_x, y, page_width - margin_x, y)
    y -= 8

    # ---------- Totaux (harmonisés) ----------
    total_ht = order.total_ht or Decimal("0")
    service_fee = order.service_fee or Decimal("0")
    delivery_fee = order.delivery_fee or Decimal("0")
    grand_total = order.grand_total or (total_ht + service_fee + delivery_fee)
    amount_paid = order.amount_paid or Decimal("0")
    amount_due = order.amount_due or Decimal("0")

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(grey, grey, grey)
    c.drawString(margin_x, y, "Total prestations :")
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
    y -= 12

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(grey, grey, grey)
    c.drawString(margin_x, y, "Montant payé :")
    c.setFillColorRGB(0, 0, 0)
    c.drawRightString(
        page_width - margin_x,
        y,
        f"{int(amount_paid):,} FCFA".replace(",", " "),
    )
    y -= 10

    c.setFont("Helvetica", 8)
    c.setFillColorRGB(grey, grey, grey)
    c.drawString(margin_x, y, "Montant dû :")
    c.setFillColorRGB(0, 0, 0)
    if amount_due > 0:
        due_txt = f"{int(amount_due):,} FCFA".replace(",", " ")
    else:
        due_txt = "0 FCFA (soldée)"
    c.drawRightString(page_width - margin_x, y, due_txt)
    y -= 18

    # ---------- URL POUR LE QR-CODE (lien direct vers ce ticket) ----------
    ticket_url = _build_order_public_url(
        request,
        order,
        viewname="orders:order_ticket_thermal_pdf",
    )

    # ---------- Génération du QR code ----------
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

    # ---------- Dessin du QR code ----------
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
        # si le QR plante, on ne bloque pas l'impression
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
    response["Content-Disposition"] = f'inline; filename=\"{filename}\"'
    return response


def order_ticket_pdf(request, order_id):
    """
    Ticket simple de la commande.
    Pour l'instant on renvoie du HTML.
    Plus tard, on pourra convertir ce HTML en PDF.
    """
    order = get_object_or_404(
        Order.objects.select_related("customer", "laundry_partner", "delivery_partner")
                     .prefetch_related("items__service", "items__photos"),
        pk=order_id,
    )

    items = order.items.all()

    context = {
        "order": order,
        "items": items,
    }
    return render(request, "orders/ticket_pdf.html", context)


def safe_decimal(value, default=Decimal("0")):
    try:
        if value in (None, ""):
            return default
        return Decimal(str(value))
    except Exception:
        return default


@transaction.atomic
def update(request, order_id):
    """
    Édition d’une commande EXISTANTE.

    Objectifs :
    - Garder les OrderItem existants + leurs photos.
    - Permettre :
        * mise à jour du statut, partenaires, notes
        * ajout de nouvelles lignes
        * suppression de lignes (diff entre DB et lignes postées)
        * ajout de nouvelles photos par ligne
    """

    order = get_object_or_404(
        Order.objects.select_related("customer", "laundry_partner", "delivery_partner"),
        pk=order_id,
    )

    # Pour l'affichage (GET) : catalogue de services
    service_categories = ServiceCategory.objects.all().order_by("name")
    service_items = (
        ServiceItem.objects.select_related("category")
        .all()
        .order_by("category__name", "name")
    )

    # Paramètres transport pour le front (mêmes noms que dans create.html)
    logistics = getattr(settings, "FAGNI_LOGISTICS", {})
    delivery_min_fee = logistics.get("client_min_fee", 0)
    delivery_price_per_km = logistics.get("client_price_per_km", 0)
    delivery_fixed_fee = logistics.get("client_fixed_fee", 0)

    if request.method == "POST":
        # --- 1) Mise à jour infos ordre (hors items) ---

        status = request.POST.get("status") or order.status
        order.status = status

        laundry_id = request.POST.get("laundry_partner") or None
        delivery_id = request.POST.get("delivery_partner") or None

        if laundry_id:
            try:
                order.laundry_partner_id = int(laundry_id)
            except (TypeError, ValueError):
                pass

        if delivery_id:
            try:
                order.delivery_partner_id = int(delivery_id)
            except (TypeError, ValueError):
                pass

        # notes / instructions
        notes_raw = request.POST.get("order_notes", None)
        if notes_raw is not None:
            order.notes = notes_raw.strip()

        # --- 2) Gestion des lignes (update / ajout / suppression) ---

        item_indexes = request.POST.getlist("item_index[]")
        item_ids = request.POST.getlist("item_id[]")
        service_ids = request.POST.getlist("service_id[]")
        designations = request.POST.getlist("designation[]")
        quantities = request.POST.getlist("quantity[]")
        unit_prices = request.POST.getlist("unit_price[]")

        # Items existants en DB
        existing_items_qs = (
            order.items.all()
            .select_related("service")
            .prefetch_related("photos")
            .order_by("id")
        )
        existing_by_id = {str(it.id): it for it in existing_items_qs}

        kept_ids = set()

        nb_rows = len(service_ids)

        for idx in range(nb_rows):
            raw_service_id = (service_ids[idx] or "").strip()
            raw_designation = (designations[idx] or "").strip()
            raw_item_id = (item_ids[idx] or "").strip()
            raw_quantity = (quantities[idx] or "").strip()
            raw_unit_price = (unit_prices[idx] or "").strip()
            raw_index = (item_indexes[idx] or "").strip()

            existing_item = existing_by_id.get(raw_item_id) if raw_item_id else None

            # 2.a Ancienne ligne sans service_id dans le POST :
            #    on la garde telle quelle (utile pour vieilles commandes)
            if not raw_service_id and existing_item:
                kept_ids.add(existing_item.id)

                # nouvelles photos éventuelles
                if raw_index != "":
                    files = request.FILES.getlist(f"photos_{raw_index}")
                    for f in files:
                        OrderItemPhoto.objects.create(order_item=existing_item, image=f)

                continue

            # Si pas de service_id et pas de ligne existante -> ignore
            if not raw_service_id:
                continue

            # quantité
            try:
                quantity = int(raw_quantity)
            except (TypeError, ValueError):
                quantity = 0

            # prix unitaire (Decimal, avec nettoyage)
            clean_unit_price = (raw_unit_price or "").replace(" ", "").replace("\u00a0", "")
            try:
                unit_price = Decimal(clean_unit_price)
            except (TypeError, ValueError, InvalidOperation):
                unit_price = Decimal("0")

            # Ligne invalide (nouvelle) : on n’en crée pas
            if quantity <= 0 or unit_price <= 0:
                # Si c’est une ligne existante, on la garde telle quelle
                if existing_item:
                    kept_ids.add(existing_item.id)
                    if raw_index != "":
                        files = request.FILES.getlist(f"photos_{raw_index}")
                        for f in files:
                            OrderItemPhoto.objects.create(order_item=existing_item, image=f)
                continue

            designation = raw_designation

            # --- 2b) Ligne existante ou nouvelle ? ---
            if existing_item:
                existing_item.service_id = int(raw_service_id)
                if designation:
                    existing_item.designation = designation
                existing_item.quantity = quantity
                existing_item.unit_price = unit_price
                existing_item.save()
                item = existing_item
            else:
                item = OrderItem.objects.create(
                    order=order,
                    service_id=int(raw_service_id),
                    designation=designation,
                    quantity=quantity,
                    unit_price=unit_price,
                )

            kept_ids.add(item.id)

            # --- 2c) Ajout de nouvelles photos pour cette ligne ---
            if raw_index != "":
                files = request.FILES.getlist(f"photos_{raw_index}")
                for f in files:
                    OrderItemPhoto.objects.create(order_item=item, image=f)

        # --- 3) Suppression des lignes qui ne sont plus envoyées ---
        # On ne supprime que si la ligne a complètement disparu du POST
        for it in existing_items_qs:
            if it.id not in kept_ids:
                it.delete()

        # --- 4) Recalcul des totaux ---
        # Si ton modèle a une méthode dédiée, on l’utilise.
        if hasattr(order, "recalculate_totals"):
            order.recalculate_totals()

        # Puis on sauvegarde l’ordre (statut, notes, partenaires, etc.)
        order.save()

        messages.success(request, "Commande mise à jour avec succès.")
        return redirect("orders:detail", order_id=order.id)

    # --- GET : affichage du formulaire ---
    context = {
        "order": order,
        "service_categories": service_categories,
        "service_items": service_items,
        "delivery_min_fee": delivery_min_fee,
        "delivery_price_per_km": delivery_price_per_km,
        "delivery_fixed_fee": delivery_fixed_fee,
    }
    return render(request, "orders/update.html", context)


# ============================================================
#  TABLEAU DE BORD LIVREURS
# ============================================================
@login_required
def driver_dashboard(request):
    """
    Tableau 'Commandes avec livreurs' :
    - Filtre par livreur (delivery_partner)
    - KPIs : nombre de commandes, statuts, distance totale, coût livreur, marge logistique
    """

    # 1) Récupération du filtre livreur (GET ?driver_id=...)
    driver_id = request.GET.get("driver_id") or ""

    # 2) Liste des livreurs disponibles pour le select
    drivers = DeliveryPartner.objects.all().order_by("name")

    # 3) Base queryset : uniquement les commandes avec un livreur associé
    base_qs = (
        Order.objects
        .select_related("customer", "delivery_partner", "laundry_partner")
        .filter(delivery_partner__isnull=False)
    )

    # 4) Application éventuelle du filtre livreur
    if driver_id:
        base_qs = base_qs.filter(delivery_partner_id=driver_id)

    # 5) Ordre d'affichage : plus récentes d'abord
    orders_qs = base_qs.order_by("-created_at")

    # 6) KPIs statistiques sur les commandes filtrées
    total_orders = orders_qs.count()

    pending = orders_qs.filter(status="pending").count()
    in_progress = orders_qs.filter(status="in_progress").count()
    done = orders_qs.filter(status="done").count()
    canceled = orders_qs.filter(status="canceled").count()

    agg = orders_qs.aggregate(
        dist=Sum("distance_km"),
        driver_cost=Sum("driver_logistic_cost"),
        margin=Sum("logistic_margin"),
    )

    total_distance = agg.get("dist") or 0
    total_driver_cost = agg.get("driver_cost") or 0
    total_logistic_margin = agg.get("margin") or 0

    # 7) Contexte pour le template
    try:
        selected_driver_id = int(driver_id) if driver_id else None
    except ValueError:
        selected_driver_id = None

    context = {
        "drivers": drivers,
        "selected_driver_id": selected_driver_id,
        "orders": orders_qs,
        # KPIs
        "total_orders": total_orders,
        "pending": pending,
        "in_progress": in_progress,
        "done": done,
        "canceled": canceled,
        "total_distance": total_distance,
        "total_driver_cost": total_driver_cost,
        "total_logistic_margin": total_logistic_margin,
    }

    return render(request, "orders/driver_dashboard.html", context)


# ------------------------------------------------------------
# APP LIVREUR – "Mes courses du jour"
# ------------------------------------------------------------
def _gmaps_url(lat, lng):
    """
    Construit une URL Google Maps simple à partir d'une latitude/longitude.

    - Si lat/lng sont vides ou invalides → retourne ""
    - Sinon → https://www.google.com/maps?q=lat,lng
    """
    try:
        if lat in (None, "") or lng in (None, ""):
            return ""
        lat_f = float(lat)
        lng_f = float(lng)
        return f"https://www.google.com/maps?q={lat_f},{lng_f}"
    except Exception:
        return ""


def _get_driver_app_context(request):
    """
    Logique commune pour la vue HTML et la vue JSON (AJAX) de l'app livreur.
    Gère :
    - mode livreur (user non staff, lié à un DeliveryPartner par email)
    - mode staff (user staff, choisit un livreur dans la liste)
    - filtres de statut
    """
    user = request.user

    # Tous les livreurs (utile en mode staff)
    drivers = DeliveryPartner.objects.all().order_by("name")

    # Filtres venant de l'URL
    status_filter = request.GET.get("status", "active")
    selected_driver_id = request.GET.get("driver_id") or None

    driver_mode = False
    current_driver = None

    # 🔒 Mode LIVREUR : user connecté NON staff → on le mappe à un DeliveryPartner via son email
    if user.is_authenticated and not user.is_staff:
        email = (user.email or "").strip()
        if email:
            try:
                current_driver = DeliveryPartner.objects.get(email__iexact=email)
                driver_mode = True
                selected_driver_id = str(current_driver.id)
            except DeliveryPartner.DoesNotExist:
                driver_mode = False

    # Base queryset
    orders_qs = Order.objects.select_related(
        "customer",
        "laundry_partner",
        "delivery_partner",
    )

    # Filtre par livreur
    if driver_mode and current_driver:
        # Le livreur ne voit QUE ses propres courses
        orders_qs = orders_qs.filter(delivery_partner=current_driver)
    elif selected_driver_id:
        orders_qs = orders_qs.filter(delivery_partner_id=selected_driver_id)

    # Filtre par statut
    if status_filter == "active":
        orders_qs = orders_qs.filter(status__in=["pending", "in_progress"])
    elif status_filter == "done":
        orders_qs = orders_qs.filter(status="done")
    elif status_filter == "canceled":
        orders_qs = orders_qs.filter(status="canceled")
    # "all" → pas de filtre de statut

    orders_qs = orders_qs.order_by("-created_at")

    # Aujourd'hui
    today = timezone.localdate()
    today_qs = orders_qs.filter(created_at__date=today)

    # Agrégats
    aggregates = orders_qs.aggregate(
        total_distance_km=Sum("distance_km"),
        total_driver_income=Sum("driver_logistic_cost"),
    )

    context = {
        # données principales
        "orders": orders_qs,
        "drivers": drivers,
        "selected_driver_id": int(selected_driver_id) if selected_driver_id else None,
        "status_filter": status_filter,

        # mode / identité livreur
        "driver_mode": driver_mode,
        "current_driver": current_driver,

        # KPIs
        "filtered_orders_count": orders_qs.count(),
        "total_orders": orders_qs.count(),     # ici, on considère "total" = après filtres livreur+statut
        "today_orders": today_qs.count(),
        "pending": orders_qs.filter(status="pending").count(),
        "in_progress": orders_qs.filter(status="in_progress").count(),
        "done": orders_qs.filter(status="done").count(),
        "canceled": orders_qs.filter(status="canceled").count(),
        "total_distance_km": aggregates["total_distance_km"] or 0,
        "total_driver_income": aggregates["total_driver_income"] or 0,
    }
    return context


@login_required
def order_scan_redirect(request, order_code):
    """
    Lors d’un scan QR, on retrouve la commande via son code unique.
    Puis redirection vers la vue livreur, si le livreur est bien assigné.
    """
    # 1) Retrouver la commande
    order = get_object_or_404(
        Order,
        code__iexact=order_code
    )

    # 2) Vérifier le livreur connecté
    user_email = (request.user.email or "").strip().lower()

    try:
        delivery_partner = DeliveryPartner.objects.get(email__iexact=user_email)
    except DeliveryPartner.DoesNotExist:
        return HttpResponseForbidden(
            "Aucun profil livreur associé à cet email (%s)." % user_email
        )

    # 3) Vérifier que c’est bien SA course
    if order.delivery_partner_id != delivery_partner.id:
        return HttpResponseForbidden("Vous n’êtes pas assigné à cette course.")

    # 4) Redirection vers sa page
    return redirect("orders:driver_order_detail", order_id=order.id)


@login_required
def driver_order_timeline_action(request, order_id, action):
    """
    App livreur – actions chronométrées sur la timeline :
    - pickup_done       -> pickup_time
    - dropoff_done      -> dropoff_time
    - wash_done         -> wash_complete_time
    - return_done       -> return_time
    - delivered_done    -> delivered_time

    Sécurisé :
    - staff : peut tout faire
    - livreur : uniquement sur SES courses
    """
    if request.method != "POST":
        return redirect("orders:driver_order_detail", order_id=order_id)

    order = get_object_or_404(
        Order.objects.select_related("delivery_partner"),
        pk=order_id,
    )

    # --- Sécurité accès ---
    user = request.user

    # Staff : OK sur tout
    if not user.is_staff:
        user_email = (user.email or "").strip()
        if not user_email:
            return HttpResponseForbidden("Votre compte n'a pas d'email défini.")

        try:
            delivery_partner = DeliveryPartner.objects.get(email__iexact=user_email)
        except DeliveryPartner.DoesNotExist:
            return HttpResponseForbidden(
                "Aucun profil livreur associé à cet email."
            )

        if order.delivery_partner_id != delivery_partner.id:
            return HttpResponseForbidden("Vous n'êtes pas assigné à cette course.")

    # --- Mapping action -> champ datetime ---
    action_map = {
        "pickup_done": "pickup_time",
        "dropoff_done": "dropoff_time",
        "wash_done": "wash_complete_time",
        "return_done": "return_time",
        "delivered_done": "delivered_time",
    }

    field_name = action_map.get(action)
    if not field_name:
        # action inconnue : on retourne au détail
        return redirect("orders:driver_order_detail", order_id=order_id)

    # On ne modifie que si le champ est encore vide (idempotent)
    current_val = getattr(order, field_name, None)
    if not current_val:
        now = timezone.now()
        setattr(order, field_name, now)
        # On essaye d'update updated_at s'il existe
        update_fields = [field_name]
        if hasattr(order, "updated_at"):
            order.updated_at = now
            update_fields.append("updated_at")
        order.save(update_fields=update_fields)

    next_url = request.POST.get("next") or reverse(
        "orders:driver_order_detail",
        args=[order_id],
    )
    return redirect(next_url)


@login_required
def driver_performance(request, driver_id):
    """
    Dashboard performance d’un livreur FAGNI :
    - nombre de courses
    - distances A/R
    - revenus livreur
    - stats de statut
    - temps moyen d’une course
    """
    driver = get_object_or_404(DeliveryPartner, pk=driver_id)

    # Toutes les commandes assignées à ce livreur
    orders_qs = (
        Order.objects.filter(delivery_partner=driver)
        .select_related("customer")
        .order_by("-created_at")
    )

    total_orders = orders_qs.count()

    # Agrégats simples (sans amount_paid, qui n'existe pas dans Order)
    aggregates = orders_qs.aggregate(
        total_distance_km=Sum("distance_km"),
        total_driver_income=Sum("driver_logistic_cost"),
    )

    total_distance_km = aggregates["total_distance_km"] or 0
    total_driver_income = aggregates["total_driver_income"] or 0

    # Montant total client TTC (total + service_fee + delivery_fee)
    grand_total_client = 0
    for o in orders_qs:
        grand_total_client += (o.total or 0) + (o.service_fee or 0) + (o.delivery_fee or 0)

    # Moyennes
    distance_per_order = total_distance_km / total_orders if total_orders else 0
    income_per_order = total_driver_income / total_orders if total_orders else 0

    # Statuts
    done_count = orders_qs.filter(status="done").count()
    canceled_count = orders_qs.filter(status="canceled").count()
    pending_count = orders_qs.filter(status="pending").count()
    in_progress_count = orders_qs.filter(status="in_progress").count()

    done_ratio = (done_count / total_orders * 100) if total_orders else 0

    # Durée moyenne d’une course (pickup -> delivered)
    duration_qs = orders_qs.filter(
        pickup_time__isnull=False,
        delivered_time__isnull=False,
    ).annotate(
        duration=ExpressionWrapper(
            F("delivered_time") - F("pickup_time"),
            output_field=DurationField(),
        )
    )

    avg_duration_td = None
    avg_duration_minutes = None
    if duration_qs.exists():
        duration_agg = duration_qs.aggregate(avg_duration=Avg("duration"))
        avg_duration_td = duration_agg["avg_duration"]
        if avg_duration_td:
            total_seconds = avg_duration_td.total_seconds()
            avg_duration_minutes = round(total_seconds / 60)

    context = {
        "driver": driver,
        "total_orders": total_orders,
        "total_distance_km": total_distance_km,
        "distance_per_order": distance_per_order,
        "total_driver_income": total_driver_income,
        "income_per_order": income_per_order,
        "grand_total_client": grand_total_client,
        "done_count": done_count,
        "canceled_count": canceled_count,
        "pending_count": pending_count,
        "in_progress_count": in_progress_count,
        "done_ratio": done_ratio,
        "avg_duration_minutes": avg_duration_minutes,
        # On affiche une liste de courses (limite raisonnable)
        "orders": orders_qs[:50],
    }

    return render(request, "orders/driver_performance.html", context)


@login_required
def driver_app_export_xlsx(request):
    """
    Export Excel 'ultra pro' des courses livreur,
    avec charte graphique FAGNI.
    - Si connecté en livreur => uniquement ses courses
    - Si staff => peut filtrer sur un livreur via ?driver_id=
    """
    user = request.user
    user_email = (user.email or "").strip()

    from partners.models import DeliveryPartner  # au cas où l'import n'est pas global

    connected_driver = None
    if user_email:
        try:
            connected_driver = DeliveryPartner.objects.get(email__iexact=user_email)
        except DeliveryPartner.DoesNotExist:
            connected_driver = None

    orders_qs = (
        Order.objects.select_related(
            "customer",
            "delivery_partner",
            "laundry_partner",
        )
        .all()
        .order_by("-created_at")
    )

    # --- Filtre livreur ---
    selected_driver_id = request.GET.get("driver_id") or None

    if connected_driver and not user.is_staff:
        # Mode LIVREUR : il ne voit QUE ses commandes
        orders_qs = orders_qs.filter(delivery_partner=connected_driver)
    else:
        # Mode OPS / STAFF : filtre optionnel sur un livreur précis
        if selected_driver_id:
            orders_qs = orders_qs.filter(delivery_partner_id=selected_driver_id)

    # --- Filtre statut ---
    status_filter = request.GET.get("status", "active")
    if status_filter == "active":
        orders_qs = orders_qs.filter(status__in=["pending", "in_progress"])
    elif status_filter in ["done", "canceled"]:
        orders_qs = orders_qs.filter(status=status_filter)
    elif status_filter == "all":
        pass
    else:
        orders_qs = orders_qs.filter(status__in=["pending", "in_progress"])

    # =======================
    # 1) Création du workbook
    # =======================
    wb = Workbook()
    ws = wb.active
    ws.title = "Courses livreur"

    # Couleurs FAGNI
    BLUE = "173B63"
    ORANGE = "F07C22"
    LIGHT_BG = "F8FAFC"
    HEADER_BG = "E5E7EB"

    # Styles
    title_font = Font(size=14, bold=True, color=BLUE)
    header_font = Font(size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor=BLUE)
    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )
    cell_font = Font(size=10, color="111827")

    # =======================
    # 2) Titre + sous-titre
    # =======================
    ws["A1"] = "FAGNI – Suivi des courses livreur"
    ws["A1"].font = title_font
    ws.merge_cells("A1:M1")

    info_txt = "Export généré le " + timezone.localtime(timezone.now()).strftime(
        "%d/%m/%Y %H:%M"
    )
    if connected_driver and not user.is_staff:
        info_txt += f" • Livreur : {connected_driver.name}"
    ws["A2"] = info_txt
    ws["A2"].font = Font(size=9, color="6B7280")
    ws.merge_cells("A2:M2")

    start_row = 4

    # =======================
    # 3) Ligne d’en-tête
    # =======================
    headers = [
        "Code",
        "Client",
        "Téléphone",
        "Adresse",
        "Statut",
        "Créée le",
        "Distance A/R (km)",
        "Total prestations",
        "Service FAGNI",
        "Livraison",
        "Total global client",
        "Livreur",
        "Blanchisserie",
    ]

    for col_index, header in enumerate(headers, start=1):
        cell = ws.cell(row=start_row, column=col_index, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin_border

    # =======================
    # 4) Lignes de données
    # =======================
    row = start_row + 1
    for order in orders_qs:
        total_client = (order.total or 0) + (order.service_fee or 0) + (order.delivery_fee or 0)

        values = [
            order.code or order.id,
            getattr(order.customer, "name", "") or "",
            getattr(order.customer, "phone", "") or "",
            getattr(order.customer, "address", "") or "",
            order.get_status_display(),
            order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else "",
            float(order.distance_km or 0),
            float(order.total or 0),
            float(order.service_fee or 0),
            float(order.delivery_fee or 0),
            float(total_client),
            order.delivery_partner.name if order.delivery_partner else "",
            order.laundry_partner.name if order.laundry_partner else "",
        ]

        for col_index, value in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col_index, value=value)
            cell.font = cell_font
            cell.alignment = Alignment(
                horizontal="left",
                vertical="center",
                wrap_text=True,
            )
            cell.border = thin_border

        row += 1

    last_row = row - 1

    # =======================
    # 5) Formatage colonnes
    # =======================
    from openpyxl.utils import get_column_letter

    col_widths = {
        1: 14,   # Code
        2: 22,   # Client
        3: 14,   # Téléphone
        4: 30,   # Adresse
        5: 14,   # Statut
        6: 18,   # Créée le
        7: 16,   # Distance
        8: 18,   # Total prestation
        9: 16,   # Service
        10: 16,  # Livraison
        11: 20,  # Total global
        12: 20,  # Livre
        13: 20,  # Blanchisserie
    }

    for col_index, width in col_widths.items():
        ws.column_dimensions[get_column_letter(col_index)].width = width

    # Filtres + freeze panes
    ws.auto_filter.ref = f"A{start_row}:M{last_row}"
    ws.freeze_panes = ws["A5"]  # fige la ligne d’en-tête

    # =======================
    # 6) Réponse HTTP
    # =======================
    filename = "fagni_driver_app_export.xlsx"
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename=\"{filename}\"'
    wb.save(response)
    return response


@login_required
def driver_app_export_csv(request):
    """
    Export CSV des commandes de l'App livreur,
    avec les mêmes filtres que driver_app :
    - driver_id
    - status
    - date_from / date_to
    """

    driver_id = request.GET.get("driver_id") or ""
    status_filter = request.GET.get("status") or "active"
    date_from_str = request.GET.get("date_from") or ""
    date_to_str = request.GET.get("date_to") or ""

    date_from = None
    date_to = None

    if date_from_str:
        try:
            date_from = datetime.strptime(date_from_str, "%Y-%m-%d").date()
        except ValueError:
            date_from = None

    if date_to_str:
        try:
            date_to = datetime.strptime(date_to_str, "%Y-%m-%d").date()
        except ValueError:
            date_to = None

    # --- Base queryset comme dans driver_app ---
    base_qs = (
        Order.objects.select_related(
            "customer",
            "laundry_partner",
            "delivery_partner",
        )
        .prefetch_related("items__service")
        .order_by("-created_at")
    )

    # Filtre période
    if date_from:
        base_qs = base_qs.filter(created_at__date__gte=date_from)
    if date_to:
        base_qs = base_qs.filter(created_at__date__lte=date_to)

    # Filtre livreur
    if driver_id:
        try:
            driver_id_int = int(driver_id)
        except ValueError:
            driver_id_int = None

        if driver_id_int:
            qs_driver = base_qs.filter(delivery_partner_id=driver_id_int)
        else:
            qs_driver = base_qs
    else:
        qs_driver = base_qs

    # Filtre statut (même logique que driver_app)
    if status_filter == "active":
        qs_display = qs_driver.filter(status__in=["pending", "in_progress"])
    elif status_filter == "done":
        qs_display = qs_driver.filter(status="done")
    elif status_filter == "canceled":
        qs_display = qs_driver.filter(status="canceled")
    else:
        qs_display = qs_driver

    # Annotation montant client (total + service + livraison)
    total_client_expr = ExpressionWrapper(
        F("total") + F("service_fee") + F("delivery_fee"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    qs_with_amount = qs_display.annotate(total_client_amount=total_client_expr)

    # --- Construction de la réponse CSV ---
    now = timezone.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    filename = f"fagni_driver_app_{ts}.csv"

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    # séparateur ; pour Excel FR
    writer = csv.writer(response, delimiter=";")

    # En-têtes
    writer.writerow([
        smart_str("ID commande"),
        smart_str("Code"),
        smart_str("Créée le"),
        smart_str("Statut"),
        smart_str("Livreur"),
        smart_str("Client"),
        smart_str("Téléphone client"),
        smart_str("Adresse client"),
        smart_str("Blanchisserie"),
        smart_str("Distance A/R (km)"),
        smart_str("Montant client (FCFA)"),
        smart_str("Revenu livreur (FCFA)"),
    ])

    # Lignes
    for o in qs_with_amount:
        created_str = o.created_at.strftime("%d/%m/%Y %H:%M") if o.created_at else ""
        status_label = o.get_status_display() if hasattr(o, "get_status_display") else o.status

        distance_km = o.distance_km or 0
        total_client = o.total_client_amount or 0
        driver_income = o.driver_logistic_cost or 0

        writer.writerow([
            smart_str(o.id),
            smart_str(o.code or o.id),
            smart_str(created_str),
            smart_str(status_label),
            smart_str(o.delivery_partner.name if o.delivery_partner else ""),
            smart_str(o.customer.name if o.customer else ""),
            smart_str(o.customer.phone if o.customer else ""),
            smart_str(o.customer.address if o.customer else ""),
            smart_str(o.laundry_partner.name if o.laundry_partner else ""),
            f"{float(distance_km):.2f}",
            f"{float(total_client):.0f}",
            f"{float(driver_income):.0f}",
        ])

    return response


def compute_driver_week_stats(driver):
    """
    Calcule les stats SEMAINE (lundi -> aujourd'hui) pour un livreur donné.
    Utilisé par :
    - driver_me_app
    - driver_leaderboard

    On s'aligne sur une logique simple :
    - Périmètre : toutes les commandes de la semaine pour ce livreur
    - Revenus estimés = somme de driver_logistic_cost sur la semaine
    - Distance = somme de distance_km sur la semaine
    - Prime = 500 FCFA * nb de courses TERMINÉES dans la semaine
    - Taux de succès = done / (done + canceled) sur la semaine
    """
    today = timezone.localdate()
    start_week = today - timedelta(days=today.weekday())

    # Toutes les commandes de la semaine pour ce livreur
    week_qs = Order.objects.filter(
        delivery_partner=driver,
        created_at__date__gte=start_week,
        created_at__date__lte=today,
    )

    # Sous-ensembles
    done_qs = week_qs.filter(status="done")
    canceled_qs = week_qs.filter(status="canceled")

    # 1) Distance et revenus estimés sur TOUTES les commandes de la semaine
    weekly_distance_km = (
        week_qs.aggregate(s=Sum("distance_km"))["s"] or Decimal("0.0")
    )
    weekly_earnings = (
        week_qs.aggregate(s=Sum("driver_logistic_cost"))["s"] or Decimal("0.0")
    )

    # 2) Nombre de courses terminées (pour la prime)
    weekly_orders = done_qs.count()

    # 3) Objectifs
    weekly_target_orders = 40
    weekly_target_earnings = 80000  # FCFA

    def pct(part, total):
        if not total:
            return 0
        return int(Decimal(part) * Decimal(100) / Decimal(total))

    weekly_orders_progress = pct(weekly_orders, weekly_target_orders)
    weekly_earnings_progress = pct(weekly_earnings, weekly_target_earnings)

    # 4) Prime : 500 FCFA par course terminée dans la semaine
    weekly_bonus_amount = weekly_orders * 500

    # 5) Taux de succès = done / (done + canceled)
    total_finished_or_canceled = done_qs.count() + canceled_qs.count()
    if total_finished_or_canceled > 0:
        weekly_success_rate = int(
            Decimal(done_qs.count())
            * Decimal(100)
            / Decimal(total_finished_or_canceled)
        )
    else:
        weekly_success_rate = 0

    # 6) Heures de pointe (à affiner plus tard)
    weekly_peak_rides = 0

    return {
        "start_week": start_week,
        "end_week": today,
        "weekly_distance_km": weekly_distance_km,
        "weekly_earnings": weekly_earnings,
        "weekly_orders": weekly_orders,
        "weekly_target_orders": weekly_target_orders,
        "weekly_target_earnings": weekly_target_earnings,
        "weekly_orders_progress": weekly_orders_progress,
        "weekly_earnings_progress": weekly_earnings_progress,
        "weekly_bonus_amount": weekly_bonus_amount,
        "weekly_success_rate": weekly_success_rate,
        "weekly_peak_rides": weekly_peak_rides,
    }


@login_required
def driver_me_app(request):
    """
    Vue 'Mes courses du jour' pour le livreur connecté.

    - Récupère le DeliveryPartner via son email.
    - Liste TOUTES ses commandes (les plus récentes en premier).
    - Les KPI "du jour" ne filtrent que sur la date du jour,
      mais la liste affiche les commandes actives du jour
      OU, si aucune, les dernières commandes tout court.
    """
    user = request.user

    # 1) Récupérer le profil livreur
    delivery_partner = get_object_or_404(DeliveryPartner, email=user.email)

    # 2) Date du jour
    today = timezone.localdate()

    # 3) Queryset de base : toutes les commandes du livreur
    base_qs = Order.objects.filter(delivery_partner=delivery_partner).order_by("-created_at")

    # 4) Commandes "du jour" (actives)
    today_qs = base_qs.filter(created_at__date=today)

    # Si on a des commandes aujourd'hui, on affiche celles-là,
    # sinon on affiche les dernières commandes tout court.
    if today_qs.exists():
        orders = today_qs
    else:
        orders = base_qs[:20]  # éviter une liste trop longue

    # 5) KPI du jour (toujours sur "today_qs")
    total_today = today_qs.count()
    pending = today_qs.filter(status="pending").count()
    in_progress = today_qs.filter(status="in_progress").count()
    done = today_qs.filter(status="done").count()
    canceled = today_qs.filter(status="canceled").count()

    # KPI distance & revenus estimés (jour)
    agg = today_qs.aggregate(
        total_distance_km=models.Sum("distance_km"),
        total_driver_cost=models.Sum("driver_logistic_cost"),
    )
    total_distance_km = agg["total_distance_km"] or 0
    driver_earnings = agg["total_driver_cost"] or 0

    # KPI semaine (simple pour l’instant : semaine courant)
    start_week = today - timezone.timedelta(days=today.weekday())
    end_week = start_week + timezone.timedelta(days=7)

    week_qs = base_qs.filter(
        created_at__date__gte=start_week,
        created_at__date__lt=end_week,
    )

    weekly_orders = week_qs.count()
    weekly_agg = week_qs.aggregate(
        total_driver_cost=models.Sum("driver_logistic_cost"),
    )
    weekly_earnings = weekly_agg["total_driver_cost"] or 0

    # Petits objectifs arbitraires
    weekly_target_orders = 40
    weekly_target_earnings = 80000

    weekly_orders_progress = int(min(100, (weekly_orders / weekly_target_orders) * 100)) if weekly_target_orders else 0
    weekly_earnings_progress = int(min(100, (weekly_earnings / weekly_target_earnings) * 100)) if weekly_target_earnings else 0

    # Prime semaine : pour l’instant on réutilise la logique existante côté leaderboard,
    # mais on peut la raffiner plus tard.
    weekly_bonus_amount = 0
    weekly_success_rate = 0
    weekly_peak_rides = 0

    if week_qs.exists():
        done_week = week_qs.filter(status="done").count()
        weekly_success_rate = int((done_week / week_qs.count()) * 100) if week_qs.count() > 0 else 0
        weekly_peak_rides = week_qs.filter(status="done").count()
        weekly_bonus_amount = done_week * 500  # même logique : 500 FCFA / course terminée

    context = {
        "delivery_partner": delivery_partner,
        "orders": orders,

        "total_today": total_today,
        "pending": pending,
        "in_progress": in_progress,
        "done": done,
        "canceled": canceled,
        "total_distance_km": total_distance_km,
        "driver_earnings": driver_earnings,

        "weekly_orders": weekly_orders,
        "weekly_earnings": weekly_earnings,
        "weekly_target_orders": weekly_target_orders,
        "weekly_target_earnings": weekly_target_earnings,
        "weekly_orders_progress": weekly_orders_progress,
        "weekly_earnings_progress": weekly_earnings_progress,
        "weekly_bonus_amount": weekly_bonus_amount,
        "weekly_success_rate": weekly_success_rate,
        "weekly_peak_rides": weekly_peak_rides,
    }

    # Petit debug
    print("DEBUG driver_me_app – commandes affichées :", orders.count())

    return render(request, "orders/driver_me.html", context)


# orders/views.py

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Sum
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from .models import Order, DeliveryPartner  # adapte si ton import est différent


# ===========================================================
#  OUTIL INTERNE : récupérer le livreur connecté (si existe)
# ===========================================================
def _get_connected_driver(request):
    """
    Essaie d'associer l'utilisateur connecté à un DeliveryPartner
    via son email (exact ou insensible à la casse).
    Renvoie (driver or None).
    """
    user = request.user
    if not user.is_authenticated:
        return None

    email = (user.email or "").strip()
    if not email:
        return None

    try:
        return DeliveryPartner.objects.get(email__iexact=email)
    except DeliveryPartner.DoesNotExist:
        return None


# ===============================================
#  HUB LIVREUR – POINT D’ENTRÉE / TABLEAU DE BORD
# ===============================================
@login_required
def driver_hub(request):
    """
    Hub livreur FAGNI :
    - Si un DeliveryPartner est associé à l'email -> dashboard perso
    - Stats filtrées par période (today / 7d / 30d / all)
    - Courses actives + 5 dernières courses de la période
    """
    connected_driver = _get_connected_driver(request)

    stats = None
    today_orders_count = 0
    last_orders = []
    active_orders = []
    period = request.GET.get("period", "all")  # today | 7d | 30d | all
    period_label = "Depuis le début"

    if connected_driver:
        # Toutes les commandes de CE livreur (historique)
        driver_orders_qs = Order.objects.filter(delivery_partner=connected_driver)

        # Total historique
        global_total_orders = driver_orders_qs.count()

        # Période de stats
        now = timezone.now()
        period_qs = driver_orders_qs

        if period == "today":
            today = timezone.localdate()
            today_start = timezone.make_aware(
                timezone.datetime.combine(today, timezone.datetime.min.time())
            )
            today_end = timezone.make_aware(
                timezone.datetime.combine(today, timezone.datetime.max.time())
            )
            period_qs = driver_orders_qs.filter(
                created_at__gte=today_start,
                created_at__lte=today_end,
            )
            period_label = "Aujourd'hui"
        elif period == "7d":
            start = now - timedelta(days=7)
            period_qs = driver_orders_qs.filter(created_at__gte=start)
            period_label = "7 derniers jours"
        elif period == "30d":
            start = now - timedelta(days=30)
            period_qs = driver_orders_qs.filter(created_at__gte=start)
            period_label = "30 derniers jours"
        else:
            # "all" => on garde tout
            period_qs = driver_orders_qs
            period_label = "Depuis le début"

        # Compteurs sur la période choisie
        total_orders = period_qs.count()
        done_orders = period_qs.filter(status="done").count()
        in_progress_orders = period_qs.filter(status="in_progress").count()
        pending_orders = period_qs.filter(status="pending").count()
        canceled_orders = period_qs.filter(status="canceled").count()

        # Agrégats numériques (distance / revenu) sur la période
        aggregates = period_qs.aggregate(
            distance_km=Sum("distance_km"),
            driver_income=Sum("driver_logistic_cost"),
        )

        stats = {
            "total_orders": total_orders,
            "done_orders": done_orders,
            "in_progress_orders": in_progress_orders,
            "pending_orders": pending_orders,
            "canceled_orders": canceled_orders,
            "distance_km": aggregates["distance_km"] or 0,
            "driver_income": aggregates["driver_income"] or 0,
            "global_total_orders": global_total_orders,
        }

        # Commandes du jour (quel que soit le filtre)
        today = timezone.localdate()
        today_start = timezone.make_aware(
            timezone.datetime.combine(today, timezone.datetime.min.time())
        )
        today_end = timezone.make_aware(
            timezone.datetime.combine(today, timezone.datetime.max.time())
        )

        today_orders_count = driver_orders_qs.filter(
            created_at__gte=today_start,
            created_at__lte=today_end,
        ).count()

        # ✅ Mes 5 dernières courses sur la période choisie
        last_orders = list(
            period_qs
            .select_related("customer")
            .order_by("-created_at")[:5]
        )

        # ✅ Mes courses actives (tjrs globales, peu importe la période)
        active_orders = list(
            driver_orders_qs
            .select_related("customer")
            .filter(status__in=["pending", "in_progress"])
            .order_by("created_at")[:5]
        )
    else:
        stats = None
        today_orders_count = 0
        last_orders = []
        active_orders = []
        global_total_orders = 0

    context = {
        "connected_driver": connected_driver,
        "stats": stats,
        "today_orders_count": today_orders_count,
        "last_orders": last_orders,
        "active_orders": active_orders,
        "period": period,
        "period_label": period_label,
    }
    return render(request, "orders/driver_hub.html", context)


# ===============================================
#  APP LIVREUR – LISTE DES COURSES
# ===============================================
@login_required
def driver_app(request):
    """
    App livreur – liste des commandes vues côté livreur.
    Deux modes :
    - LIVREUR connecté (DeliveryPartner lié à l'email) => filtré sur lui
    - Sinon (ops / admin) => filtre 'Livreur' disponible
    """
    connected_driver = _get_connected_driver(request)

    # Base queryset
    qs = Order.objects.select_related(
        "customer",
        "laundry_partner",
        "delivery_partner",
    )

    # --- Filtre livreur ---
    selected_driver_id = request.GET.get("driver_id", "") or ""

    if connected_driver:
        # Mode livreur : on force le filtre sur lui
        qs = qs.filter(delivery_partner=connected_driver)
        selected_driver = connected_driver
        selected_driver_id = str(connected_driver.id)
    else:
        selected_driver = None
        if selected_driver_id:
            qs = qs.filter(delivery_partner_id=selected_driver_id)

    # --- Filtre statut ---
    status_filter = request.GET.get("status", "active")
    if status_filter == "active":
        qs = qs.filter(status__in=["pending", "in_progress"])
    elif status_filter == "done":
        qs = qs.filter(status="done")
    elif status_filter == "canceled":
        qs = qs.filter(status="canceled")
    # "all" = pas de filtre

    # --- Stats / KPIs ---
    filtered_orders_count = qs.count()

    today = timezone.localdate()
    today_start = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.min.time())
    )
    today_end = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.max.time())
    )

    base_for_driver_stats = Order.objects.all()
    if selected_driver_id:
        base_for_driver_stats = base_for_driver_stats.filter(
            delivery_partner_id=selected_driver_id
        )

    total_orders = base_for_driver_stats.count()
    today_orders = base_for_driver_stats.filter(
        created_at__gte=today_start,
        created_at__lte=today_end,
    ).count()

    aggregates = qs.aggregate(
        total_distance_km=Sum("distance_km"),
        total_driver_income=Sum("driver_logistic_cost"),
    )

    total_distance_km = aggregates["total_distance_km"] or 0
    total_driver_income = aggregates["total_driver_income"] or 0

    # Liste des livreurs (pour le mode "ops")
    drivers = DeliveryPartner.objects.all().order_by("name")

    context = {
        "orders": qs.order_by("-created_at"),
        "drivers": drivers,
        "selected_driver_id": selected_driver_id,
        "status_filter": status_filter,
        "filtered_orders_count": filtered_orders_count,
        "total_orders": total_orders,
        "today_orders": today_orders,
        "pending": qs.filter(status="pending").count(),
        "in_progress": qs.filter(status="in_progress").count(),
        "done": qs.filter(status="done").count(),
        "canceled": qs.filter(status="canceled").count(),
        "total_distance_km": total_distance_km,
        "total_driver_income": total_driver_income,
        "connected_driver": selected_driver,  # ou None
    }

    return render(request, "orders/driver_app.html", context)


# ===============================================
#  APP LIVREUR – DATA JSON pour refresh KPIs
# ===============================================
@login_required
def driver_app_data(request):
    """
    Version JSON des KPIs App Livreurs.
    Utilisée par le JS en AJAX dans driver_app.html
    """
    connected_driver = _get_connected_driver(request)

    qs = Order.objects.all()

    selected_driver_id = request.GET.get("driver_id", "") or ""
    status_filter = request.GET.get("status", "active")

    if connected_driver:
        qs = qs.filter(delivery_partner=connected_driver)
    elif selected_driver_id:
        qs = qs.filter(delivery_partner_id=selected_driver_id)

    if status_filter == "active":
        qs = qs.filter(status__in=["pending", "in_progress"])
    elif status_filter == "done":
        qs = qs.filter(status="done")
    elif status_filter == "canceled":
        qs = qs.filter(status="canceled")
    # "all" => pas de filtre

    filtered_orders_count = qs.count()
    pending = qs.filter(status="pending").count()
    in_progress = qs.filter(status="in_progress").count()
    done = qs.filter(status="done").count()
    canceled = qs.filter(status="canceled").count()

    today = timezone.localdate()
    today_start = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.min.time())
    )
    today_end = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.max.time())
    )

    base_for_driver_stats = Order.objects.all()
    if connected_driver:
        base_for_driver_stats = base_for_driver_stats.filter(
            delivery_partner=connected_driver
        )
    elif selected_driver_id:
        base_for_driver_stats = base_for_driver_stats.filter(
            delivery_partner_id=selected_driver_id
        )

    total_orders = base_for_driver_stats.count()
    today_orders = base_for_driver_stats.filter(
        created_at__gte=today_start,
        created_at__lte=today_end,
    ).count()

    aggregates = qs.aggregate(
        total_distance_km=Sum("distance_km"),
        total_driver_income=Sum("driver_logistic_cost"),
    )

    total_distance_km = float(aggregates["total_distance_km"] or 0)
    total_driver_income = float(aggregates["total_driver_income"] or 0)

    data = {
        "filtered_orders_count": filtered_orders_count,
        "pending": pending,
        "in_progress": in_progress,
        "done": done,
        "canceled": canceled,
        "total_orders": total_orders,
        "today_orders": today_orders,
        "total_distance_km": round(total_distance_km, 2),
        "total_driver_income": round(total_driver_income, 2),
    }
    return JsonResponse(data)


# ===============================================
#  DÉTAIL COURSE – APP LIVREUR
# ===============================================
@login_required
def driver_order_detail(request, order_id):
    """
    Détail d’une course côté livreur :
    - Accès réservé au livreur assigné
    - Vue optimisée pour mobile
    """
    connected_driver = _get_connected_driver(request)
    if not connected_driver:
        return HttpResponseForbidden(
            "Aucun profil livreur associé à votre compte utilisateur."
        )

    order = get_object_or_404(
        Order.objects.select_related(
            "customer",
            "laundry_partner",
            "delivery_partner",
        ).prefetch_related("items__service", "items__photos"),
        pk=order_id,
    )

    # Vérif d’accès
    if order.delivery_partner_id != connected_driver.id:
        return HttpResponseForbidden("Vous n’êtes pas assigné à cette course.")

    # Timeline : étapes principales
    timeline = [
        ("Collecte chez le client", order.pickup_time),
        ("Dépôt blanchisserie", order.dropoff_time),
        ("Fin de lavage", order.wash_complete_time),
        ("Reprise blanchisserie", order.return_time),
        ("Livraison client", order.delivered_time),
    ]

    # Montants côté client
    total_client = (order.total or 0) + (order.service_fee or 0) + (order.delivery_fee or 0)
    driver_income = order.driver_logistic_cost or 0

    context = {
        "delivery_partner": connected_driver,
        "order": order,
        "timeline": timeline,
        "total_client": total_client,
        "driver_income": driver_income,
    }
    return render(request, "orders/driver_order_detail.html", context)


@login_required
def driver_kpi(request):
    """
    Vue KPI livreur :
    - Si user staff : choix du livreur + filtres
    - Si user livreur : KPI uniquement sur ses propres courses
    """
    user = request.user
    connected_driver = None
    selected_driver_id = ""

    # Liste des livreurs (pour les filtres OPS)
    drivers = DeliveryPartner.objects.all().order_by("name")

    # --- Identification du livreur connecté si NON staff ---
    if not user.is_staff:
        user_email = (user.email or "").strip()
        if not user_email:
            return HttpResponseForbidden(
                "Votre compte utilisateur n'a pas d'email défini."
            )
        try:
            connected_driver = DeliveryPartner.objects.get(email__iexact=user_email)
        except DeliveryPartner.DoesNotExist:
            return HttpResponseForbidden(
                "Aucun profil livreur associé à cet email."
            )
        selected_driver_id = connected_driver.id
    else:
        # Mode OPS : on lit le driver_id dans les filtres
        driver_id_param = request.GET.get("driver_id") or ""
        if driver_id_param:
            try:
                connected_driver = DeliveryPartner.objects.get(pk=driver_id_param)
                selected_driver_id = connected_driver.id
            except DeliveryPartner.DoesNotExist:
                selected_driver_id = ""
                connected_driver = None

    # --- Filtre période ---
    period = (request.GET.get("period") or "30d").strip()
    now = timezone.now()
    dt_start = None

    if period == "today":
        dt_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "7d":
        dt_start = now - timedelta(days=7)
    elif period == "30d":
        dt_start = now - timedelta(days=30)
    else:
        period = "all"
        dt_start = None

    # --- Base queryset ---
    orders_qs = Order.objects.select_related(
        "customer", "delivery_partner", "laundry_partner"
    ).all()

    if selected_driver_id:
        orders_qs = orders_qs.filter(delivery_partner_id=selected_driver_id)

    if dt_start:
        orders_qs = orders_qs.filter(created_at__gte=dt_start)

    # --- Agrégats globaux ---
    aggregates = orders_qs.aggregate(
        total_orders=Count("id"),
        total_distance_km=Sum("distance_km"),
        total_driver_income=Sum("driver_logistic_cost"),
    )

    total_orders = aggregates.get("total_orders") or 0
    total_distance_km = aggregates.get("total_distance_km") or 0
    total_driver_income = aggregates.get("total_driver_income") or 0

    avg_distance_km = total_distance_km / total_orders if total_orders else 0
    avg_driver_income = total_driver_income / total_orders if total_orders else 0

    # --- Répartition par statut ---
    status_counts = { "pending": 0, "in_progress": 0, "done": 0, "canceled": 0 }
    for row in orders_qs.values("status").annotate(c=Count("id")):
        status = row["status"]
        c = row["c"] or 0
        if status in status_counts:
            status_counts[status] = c

    # --- Liste des dernières courses (limite 50) ---
    latest_orders = orders_qs.order_by("-created_at")[:50]

    context = {
        "drivers": drivers,
        "connected_driver": connected_driver,
        "selected_driver_id": selected_driver_id,
        "period": period,
        "total_orders": total_orders,
        "total_distance_km": total_distance_km,
        "total_driver_income": total_driver_income,
        "avg_distance_km": avg_distance_km,
        "avg_driver_income": avg_driver_income,
        "pending": status_counts["pending"],
        "in_progress": status_counts["in_progress"],
        "done": status_counts["done"],
        "canceled": status_counts["canceled"],
        "orders": latest_orders,
    }

    return render(request, "orders/driver_kpi.html", context)


@login_required
def driver_orders_csv(request):
    """
    Export CSV des courses livreur selon filtres :
    - Si user staff : peut filtrer par livreur + statut
    - Si user livreur : uniquement ses propres courses
    """
    user = request.user
    connected_driver = None
    driver_id = None

    # --- Identification du livreur ---
    if not user.is_staff:
        # Mode LIVREUR : on retrouve le DeliveryPartner via l'email
        user_email = (user.email or "").strip()
        if not user_email:
            return HttpResponseForbidden(
                "Votre compte utilisateur n'a pas d'email défini."
            )
        try:
            connected_driver = DeliveryPartner.objects.get(email__iexact=user_email)
        except DeliveryPartner.DoesNotExist:
            return HttpResponseForbidden(
                "Aucun profil livreur associé à cet email."
            )
        driver_id = connected_driver.id
    else:
        # Mode OPS : possibilité de filtrer par livreur via ?driver_id=...
        driver_id_param = request.GET.get("driver_id") or ""
        if driver_id_param:
            try:
                driver_id = int(driver_id_param)
            except ValueError:
                driver_id = None

    # --- Filtre statut (logique similaire à driver_app) ---
    status_filter = (request.GET.get("status") or "active").strip()

    orders_qs = Order.objects.select_related(
        "customer",
        "delivery_partner",
        "laundry_partner",
    ).all()

    if driver_id:
        orders_qs = orders_qs.filter(delivery_partner_id=driver_id)

    if status_filter == "active":
        orders_qs = orders_qs.filter(status__in=["pending", "in_progress"])
    elif status_filter == "done":
        orders_qs = orders_qs.filter(status="done")
    elif status_filter == "canceled":
        orders_qs = orders_qs.filter(status="canceled")
    # status_filter == "all" => pas de filtre supplémentaire

    orders_qs = orders_qs.order_by("-created_at")

    # --- Construction de la réponse CSV ---
    response = HttpResponse(content_type="text/csv")

    filename_parts = ["courses"]
    if driver_id:
        filename_parts.append(f"driver_{driver_id}")
    filename = "_".join(filename_parts) + ".csv"

    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response, delimiter=";")

    # En-tête
    writer.writerow([
        "Code commande",
        "Date création",
        "Client",
        "Téléphone",
        "Adresse",
        "Statut",
        "Distance_km",
        "Revenu_livreur_FCFA",
    ])

    # Lignes
    for order in orders_qs:
        customer = getattr(order, "customer", None)
        writer.writerow([
            order.code or order.id,
            order.created_at.strftime("%Y-%m-%d %H:%M") if order.created_at else "",
            getattr(customer, "name", "") if customer else "",
            getattr(customer, "phone", "") if customer else "",
            getattr(customer, "address", "") if customer else "",
            order.get_status_display(),
            float(order.distance_km) if order.distance_km is not None else 0,
            float(order.driver_logistic_cost) if order.driver_logistic_cost is not None else 0,
        ])

    return response


@login_required
def driver_history_me(request):
    """
    Historique des courses du livreur connecté :
    - filtre par période (7j, 30j, mois en cours)
    - filtre par statut
    - bilan : nb courses, distance, revenus, cash à remettre
    """
    user_email = (request.user.email or "").strip()
    if not user_email:
        return HttpResponseForbidden("Votre compte utilisateur n'a pas d'email défini.")

    try:
        delivery_partner = DeliveryPartner.objects.get(email__iexact=user_email)
    except DeliveryPartner.DoesNotExist:
        return HttpResponseForbidden(
            "Aucun profil livreur associé à cet email : %s" % user_email
        )

    today = timezone.localdate()

    # Filtres
    period = request.GET.get("period") or "7d"
    status_filter = request.GET.get("status") or "all"

    # Période
    if period == "30d":
        start_date = today - timedelta(days=30)
    elif period == "month":
        start_date = today.replace(day=1)
    else:  # "7d"
        start_date = today - timedelta(days=7)

    qs = Order.objects.filter(
        delivery_partner=delivery_partner,
        created_at__date__gte=start_date,
        created_at__date__lte=today,
    ).select_related("customer", "laundry_partner")

    # Statut
    if status_filter == "active":
        qs = qs.filter(status__in=["pending", "in_progress"])
    elif status_filter in ["pending", "in_progress", "done", "canceled"]:
        qs = qs.filter(status=status_filter)

    orders = list(qs.order_by("-created_at"))

    # Agrégats / calculs par ligne
    total_orders = len(orders)
    done_count = sum(1 for o in orders if o.status == "done")
    total_distance_km = qs.aggregate(total=Sum("distance_km"))["total"] or 0

    total_driver_earnings = Decimal("0.00")
    client_total = Decimal("0.00")

    # Tarif de secours par km si driver_logistic_cost est vide
    # (on pourra le mettre dans settings plus tard)
    fallback_price_per_km = Decimal("75")

    for o in orders:
        # Montant client = prestations + service FAGNI + livraison
        total_prestations = o.total or Decimal("0.00")
        service_fee = o.service_fee or Decimal("0.00")
        delivery_fee = o.delivery_fee or Decimal("0.00")

        total_client = total_prestations + service_fee + delivery_fee
        o.total_client = total_client
        client_total += total_client

        # Revenu livreur : valeur réelle si renseignée,
        # sinon estimation simple distance * 75 FCFA
        earning = o.driver_logistic_cost or Decimal("0.00")
        if (earning == 0 or earning is None) and o.distance_km:
            earning = (o.distance_km or Decimal("0.00")) * fallback_price_per_km

        o.driver_earnings_display = earning
        total_driver_earnings += earning

    # Cash à remettre = montant client - revenus livreur
    cash_to_remit = client_total - total_driver_earnings
    if cash_to_remit < 0:
        cash_to_remit = Decimal("0.00")

    context = {
        "delivery_partner": delivery_partner,
        "orders": orders,

        "period": period,
        "status_filter": status_filter,

        "start_date": start_date,
        "end_date": today,

        "total_orders": total_orders,
        "done_count": done_count,
        "total_distance_km": total_distance_km,
        "total_driver_earnings": total_driver_earnings,
        "client_total": client_total,
        "cash_to_remit": cash_to_remit,
    }
    return render(request, "orders/driver_history.html", context)


@login_required
def driver_me_data(request):
    """
    Endpoint JSON pour l’auto-refresh des KPI de driver_me_app.
    Mapping par email entre User et DeliveryPartner.
    Inclut KPI jour + semaine + progression.
    """

    user_email = (request.user.email or "").strip()
    if not user_email:
        return JsonResponse({"error": "no_email"}, status=403)

    try:
        delivery_partner = DeliveryPartner.objects.get(email__iexact=user_email)
    except DeliveryPartner.DoesNotExist:
        return JsonResponse({"error": "no_driver_profile"}, status=403)

    today = timezone.localdate()
    start_of_week = today - timedelta(days=today.weekday())

    base_qs = Order.objects.filter(delivery_partner=delivery_partner)

    today_qs = base_qs.filter(created_at__date=today)
    week_qs = base_qs.filter(
        created_at__date__gte=start_of_week,
        created_at__date__lte=today,
    )

    total_today = today_qs.count()
    pending = today_qs.filter(status="pending").count()
    in_progress = today_qs.filter(status="in_progress").count()
    done = today_qs.filter(status="done").count()
    canceled = today_qs.filter(status="canceled").count()

    total_distance_km = today_qs.aggregate(
        total=Sum("distance_km")
    )["total"] or 0

    driver_earnings = today_qs.aggregate(
        total=Sum("driver_logistic_cost")
    )["total"] or 0

    weekly_orders = week_qs.count()
    weekly_earnings = week_qs.aggregate(
        total=Sum("driver_logistic_cost")
    )["total"] or 0

    WEEKLY_TARGET_ORDERS = 40
    WEEKLY_TARGET_EARNINGS = 80000

    def compute_progress(value, target):
        if not target or target <= 0:
            return 0
        pct = int((value / target) * 100)
        return 100 if pct > 100 else pct

    weekly_orders_progress = compute_progress(weekly_orders, WEEKLY_TARGET_ORDERS)
    weekly_earnings_progress = compute_progress(
        float(weekly_earnings or 0), float(WEEKLY_TARGET_EARNINGS)
    )

    data = {
        # jour
        "total_today": total_today,
        "pending": pending,
        "in_progress": in_progress,
        "done": done,
        "canceled": canceled,
        "total_distance_km": float(total_distance_km),
        "driver_earnings": float(driver_earnings),

        # semaine
        "weekly_orders": weekly_orders,
        "weekly_earnings": float(weekly_earnings or 0),
        "weekly_target_orders": WEEKLY_TARGET_ORDERS,
        "weekly_target_earnings": WEEKLY_TARGET_EARNINGS,
        "weekly_orders_progress": weekly_orders_progress,
        "weekly_earnings_progress": weekly_earnings_progress,
    }

    return JsonResponse(data)


@login_required
def driver_leaderboard(request):
    """
    Classement des livreurs.
    Utilise compute_driver_week_stats pour :
    - revenus semaine
    - distance semaine
    - prime semaine
    - taux de succès semaine
    afin d'être 100% cohérent avec /orders/driver/me/.
    """
    today = timezone.localdate()
    start_week = today - timedelta(days=today.weekday())

    drivers = DeliveryPartner.objects.filter(is_active=True).order_by("name")

    leaderboard = []

    for d in drivers:
        week_stats = compute_driver_week_stats(d)

        leaderboard.append({
            "id": d.id,
            "name": d.name,
            "city": d.city,
            # total commandes semaine terminées
            "orders_count": week_stats["weekly_orders"],
            # pour affichage des courses terminées dans le tableau
            "done_count": week_stats["weekly_orders"],
            # taux de succès semaine
            "success_rate": week_stats["weekly_success_rate"],
            # distance semaine
            "total_distance_km": week_stats["weekly_distance_km"],
            # revenus estimés semaine
            "driver_earnings": week_stats["weekly_earnings"],
            # prime semaine (même calcul que /driver/me/)
            "bonus_amount": week_stats["weekly_bonus_amount"],
        })

    context = {
        "leaderboard": leaderboard,
        "start_week": start_week,
        "end_week": today,
    }

    return render(request, "orders/driver_leaderboard.html", context)


@login_required
def driver_me_order_detail(request, order_id):
    """
    Page détail d'une commande pour le livreur connecté.
    Vue "mobile / app livreur" avec timeline dynamique.
    """
    # On récupère le livreur connecté via son email
    delivery_partner = None
    try:
        delivery_partner = DeliveryPartner.objects.get(email=request.user.email)
    except DeliveryPartner.DoesNotExist:
        delivery_partner = None

    order = get_object_or_404(Order, pk=order_id)

    # (Optionnel) Tu peux contrôler ici que ce livreur est bien associé à la commande :
    # if delivery_partner and order.delivery_partner_id != delivery_partner.id:
    #     return HttpResponseForbidden("Cette commande ne t'est pas assignée.")

    context = {
        "order": order,
        "delivery_partner": delivery_partner,
    }
    return render(request, "orders/driver_order_detail.html", context)


@login_required
def driver_app_order_detail(request, order_id):
    """
    Détail d'une commande pour l'app livreur.

    Pour l’instant, on réutilise simplement la vue 'detail'
    afin de ne pas dupliquer la logique.
    Si plus tard tu veux un template 100 % mobile, on pourra
    faire un template dédié (ex: orders/driver_app_order_detail.html).
    """
    return detail(request, order_id)


@require_POST
@login_required
def driver_leg_action(request, leg_id, action):
    """
    Action côté livreur sur une jambe de livraison (DeliveryLeg).

    Actions possibles :
    - accept   : le livreur prend la course
    - start    : il démarre réellement la course
    - finish   : il termine la course
    - cancel   : il annule
    """
    leg = get_object_or_404(
        DeliveryLeg.objects.select_related("order"),
        pk=leg_id,
    )

    # TODO plus tard : vérifier que request.user est bien linked à leg.driver

    old_status = leg.status
    now = timezone.now()
    order = leg.order

    if action == "accept":
        # Le livreur se positionne officiellement sur la course
        if leg.status in ["pending", "assigned"]:
            leg.status = "assigned"

    elif action == "start":
        # Démarrage effectif de la course
        if leg.status in ["pending", "assigned"]:
            leg.status = "in_progress"

            # on peut tracer le début sur la commande
            if leg.leg_type == "pickup":
                if not order.pickup_time:
                    order.pickup_time = now
            elif leg.leg_type == "delivery":
                # on peut utiliser return_time comme début du retour / livraison
                if not order.return_time:
                    order.return_time = now
            order.save(update_fields=["pickup_time", "return_time"])

    elif action == "finish":
        # Course terminée
        if leg.status in ["in_progress", "assigned"]:
            leg.status = "done"

            # marquage côté commande
            if leg.leg_type == "pickup":
                # fin de la jambe client → blanchisseur
                if not order.dropoff_time:
                    order.dropoff_time = now
            elif leg.leg_type == "delivery":
                # fin de la jambe blanchisseur → client
                if not order.delivered_time:
                    order.delivered_time = now
            order.save(update_fields=["dropoff_time", "delivered_time"])

    elif action == "cancel":
        # Annulation par le livreur
        if leg.status not in ["done"]:
            leg.status = "canceled"

    else:
        return HttpResponseBadRequest("Action non reconnue")

    # Si rien n’a changé, on ne fait rien
    if leg.status == old_status:
        messages.info(request, "Aucune modification de statut pour cette course.")
        return redirect("orders:driver_app")

    leg.save()
    messages.success(request, f"Course mise à jour ({old_status} → {leg.status}).")
    return redirect("orders:driver_app")


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
@transaction.atomic
def change_status(request, order_id):
    """
    Change proprement le statut d’une commande + timestamps automatiques.
    """
    order = get_object_or_404(Order, pk=order_id)

    new_status = request.POST.get("status")
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "/"

    # Sécurité statuts valides
    valid = ["pending", "in_progress", "done", "canceled"]
    if new_status not in valid:
        messages.error(request, "Statut invalide.")
        return redirect(next_url)

    # --- workflow logique ---
    if new_status == "in_progress":
        # ne passer en cours que si pending
        if order.status == "pending":
            order.pickup_time = timezone.now()

    if new_status == "done":
        # ne terminer que si déjà en cours
        if order.status == "in_progress":
            order.delivered_time = timezone.now()

    # Mise à jour statut
    order.status = new_status
    order.save()

    messages.success(request, f"Statut mis à jour : {order.get_status_display()}")
    return redirect(next_url)


def _order_effective_total(o):
    """
    Renvoie un total "efficace" pour une commande :
    - essaie d'abord o.total
    - sinon grand_total, puis total_ttc, puis total_ht
    - retourne toujours un Decimal >= 0
    """
    candidates = [
        getattr(o, "total", None),
        getattr(o, "grand_total", None),
        getattr(o, "total_ttc", None),
        getattr(o, "total_ht", None),
    ]
    for val in candidates:
        dec = _safe_dec(val)
        if dec > DECIMAL_ZERO:
            return dec
    return DECIMAL_ZERO


@login_required
def driver_update_location(request):
    """
    Reçoit la position GPS du livreur connecté et met à jour
    les champs latitude / longitude de son DeliveryPartner.
    Appelée en AJAX depuis la driver app.
    """
    if request.method != "POST":
        return JsonResponse({"error": "Méthode non autorisée"}, status=405)

    user_email = (request.user.email or "").strip()
    if not user_email:
        return JsonResponse(
            {"error": "Utilisateur sans email, impossible de lier un livreur"},
            status=400,
        )

    try:
        dp = DeliveryPartner.objects.get(email__iexact=user_email)
    except DeliveryPartner.DoesNotExist:
        return JsonResponse(
            {"error": f"Aucun livreur associé à {user_email}"},
            status=404,
        )

    lat = request.POST.get("lat")
    lng = request.POST.get("lng")

    if not lat or not lng:
        return JsonResponse({"error": "lat et lng sont requis"}, status=400)

    try:
        dp.latitude = Decimal(str(lat))
        dp.longitude = Decimal(str(lng))
        dp.save(update_fields=["latitude", "longitude", "updated_at"])
    except Exception as e:
        return JsonResponse({"error": f"Erreur lors de la sauvegarde : {e}"}, status=400)

    return JsonResponse({"ok": True})


# orders/views.py

import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from partners.models import DeliveryPartner
from orders.models import Order


@login_required
def driver_map(request):
    """
    Carte des livreurs FAGNI.
    """

    drivers_qs = DeliveryPartner.objects.filter(
        is_active=True,
        latitude__isnull=False,
        longitude__isnull=False,
    ).order_by("name")

    drivers_data = []
    today = timezone.localdate()
    start_week = today - timezone.timedelta(days=today.weekday())

    for d in drivers_qs:
        try:
            lat = float(d.latitude)
            lng = float(d.longitude)
        except (TypeError, ValueError):
            continue

        week_orders = Order.objects.filter(
            delivery_partner=d,
            created_at__date__gte=start_week,
            created_at__date__lte=today,
            status="done",
        ).count()

        drivers_data.append({
            "id": d.id,
            "name": d.name,
            "city": d.city or "",
            "phone": d.phone or "",
            "lat": lat,
            "lng": lng,
            "week_orders": week_orders,
        })

    default_center = {"lat": 5.3453, "lng": -4.0244}
    if drivers_data:
        default_center = {
            "lat": drivers_data[0]["lat"],
            "lng": drivers_data[0]["lng"],
        }

    context = {
        "drivers": drivers_qs,
        "drivers_json": json.dumps(drivers_data),
        "default_center": default_center,
    }
    return render(request, "orders/driver_map.html", context)
