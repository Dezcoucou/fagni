from bonuses.models import BonusWeek
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import timedelta, datetime, time
from django.template.loader import render_to_string
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.exceptions import ValidationError
from django.db import models
from django.contrib import messages
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
    FloatField,
    Prefetch,
)
from django.db import transaction
from wallets.models import WalletTransaction, Wallet
from wallets.services import (
    get_or_create_wallet_for_customer,
    get_or_create_wallet_for_delivery_partner,
    get_or_create_wallet_for_laundry_partner,
    credit_wallet,
    debit_wallet,
    get_or_create_internal_wallet,  # ← celui-là manque
)
from django.db.models.functions import Coalesce, Cast, TruncDate
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.utils.dateparse import parse_date
from django.utils.encoding import smart_str

from orders.utils.pricing import compute_order_amounts
from orders.utils.settings_loader import get_pricing_settings
from orders.utils.geocoding import ensure_order_geocoded
from orders.utils.geo import resolve_pickup_coords, resolve_delivery_coords, resolve_provider_coords
from .finance import compute_order_financials
from .config_models import InvoiceSettings
from .models import (
    Order,
    Customer,
    OrderItem,
    OrderItemPhoto,
    ServiceCategory,
    ServiceItem,
    DeliveryLeg,
    OrderStatusHistory,
    haversine_distance_km,
    LogisticsConfig,
)
from .utils import auto_assign_laundry, auto_assign_delivery
from partners.models import LaundryPartner, DeliveryPartner, RelayPointPartner
from mlm.services import attach_customer_to_sponsor
from io import BytesIO
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
from math import radians, sin, cos, asin, sqrt, atan2
from weasyprint import HTML
import uuid
import os
import io
import qrcode
import csv
import json
import re


def _strip_django_comment_blocks(txt: str) -> str:
    if not txt:
        return ""
    cleaned = re.sub(r"{#.*?#}", "", str(txt), flags=re.DOTALL)
    return "\n".join([line.rstrip() for line in cleaned.splitlines() if line.strip()])

def get_invoice_settings_clean():
    """
    Retourne InvoiceSettings (singleton pk=1) avec header/footer nettoyés
    pour éviter l'affichage de {# ... #} dans les PDF/tickets.
    """
    try:
        invoice_settings, _ = InvoiceSettings.objects.get_or_create(pk=1)

        # Nettoyage pour affichage seulement (pas de save)
        invoice_settings.header_note = _strip_django_comment_blocks(
            getattr(invoice_settings, "header_note", "")
        )
        invoice_settings.footer_text = _strip_django_comment_blocks(
            getattr(invoice_settings, "footer_text", "")
        )
        return invoice_settings
    except Exception:
        return None


def get_connected_driver(request):
    """
    Essaie de retrouver le livreur connecté selon 3 logiques :
    1) ?driver_id=xx dans l'URL
    2) email du user == email du DeliveryPartner
    3) (DEV) fallback : premier livreur actif
    """
    user = request.user
    if not user.is_authenticated:
        return None

    # 1) driver_id explicite dans l'URL
    driver_id = request.GET.get("driver_id")
    if driver_id:
        d = DeliveryPartner.objects.filter(pk=driver_id, is_active=True).first()
        if d:
            return d

    # 2) mapping par email (cas normal en prod)
    if user.email:
        d = DeliveryPartner.objects.filter(email__iexact=user.email, is_active=True).first()
        if d:
            return d

    # 3) Fallback DEV : on prend le premier livreur actif
    return DeliveryPartner.objects.filter(is_active=True).order_by("id").first()


def haversine_distance_km(lat1, lng1, lat2, lng2):
    """
    Distance en kilomètres entre deux points GPS.
    Retourne None si une coordonnée manque.
    """
    if not lat1 or not lng1 or not lat2 or not lng2:
        return None

    try:
        lat1, lng1 = float(lat1), float(lng1)
        lat2, lng2 = float(lat2), float(lng2)
    except Exception:
        return None

    R = 6371.0  # Rayon de la Terre en KM

    lat1_r = radians(lat1)
    lng1_r = radians(lng1)
    lat2_r = radians(lat2)
    lng2_r = radians(lng2)

    dlat = lat2_r - lat1_r
    dlng = lng2_r - lng1_r

    a = sin(dlat/2)**2 + cos(lat1_r) * cos(lat2_r) * sin(dlng/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return Decimal(str(R * c)).quantize(Decimal("0.01"))


def generate_order_code():
    return uuid.uuid4().hex[:10].upper()

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


def recompute_order_financials(order):
    """
    Recalcule tous les montants financiers d'une commande FAGNI selon les règles métier.
    - service_fee = max(5% du sous-total prestations, 500 FCFA) si prestation > 0
    - revenu FAGNI HT = commissions + marge logistique + service_fee
    - TVA FAGNI = 18% du revenu FAGNI HT
    - revenu FAGNI TTC = revenu FAGNI HT + TVA FAGNI
    - total_client_ttc = prestations + service_fee + livraison + TVA FAGNI
    """

    ZERO = Decimal("0")
    prestation_total = order.prestation_total or ZERO
    delivery_fee = order.delivery_fee or ZERO
    commission_laundry = order.commission_laundry_ht or ZERO
    commission_delivery = order.commission_delivery_ht or ZERO
    logistic_margin = order.logistic_margin or ZERO

    # 1) Service FAGNI (HT)
    if prestation_total > ZERO:
        sf = (prestation_total * Decimal("0.05"))
        if sf < Decimal("500"):
            sf = Decimal("500")
    else:
        sf = ZERO

    # on arrondit proprement à l'unité
    sf = sf.quantize(Decimal("1."), rounding=ROUND_HALF_UP)
    order.service_fee = sf

    # 2) Revenu FAGNI HT
    fagni_ht = commission_laundry + commission_delivery + logistic_margin + sf
    order.fagni_revenue_ht = fagni_ht

    # 3) TVA FAGNI (18%)
    vat = (fagni_ht * Decimal("0.18")).quantize(Decimal("1."), rounding=ROUND_HALF_UP)
    order.vat_fagni = vat

    # 4) Revenu FAGNI TTC
    order.fagni_revenue_ttc = fagni_ht + vat

    # 5) Total TTC facturé au client
    order.total_client_ttc = prestation_total + sf + delivery_fee + vat


# ============================================================
#  HELPER : PRICING FAGNI
# ============================================================
def apply_fagni_pricing(order):
    """
    Recalcule tous les montants financiers d'une commande FAGNI selon les règles métier.

    Rappel des règles :
    - service_fee = max(5% du sous-total prestations, 500 FCFA) si prestation_total > 0, sinon 0
    - revenu FAGNI HT = commission_laundry_ht + commission_delivery_ht + logistic_margin + service_fee
    - TVA FAGNI (18%) = 18% du revenu FAGNI HT
    - revenu FAGNI TTC = revenu FAGNI HT + TVA FAGNI
    - total_client_ttc = prestation_total + service_fee + delivery_fee + TVA FAGNI
    """

    DEC = Decimal
    ZERO = DEC("0")

    # 1) Récup des bases
    prestation_total = order.prestation_total or ZERO
    delivery_fee = order.delivery_fee or ZERO

    commission_laundry = getattr(order, "commission_laundry_ht", ZERO) or ZERO
    commission_delivery = getattr(order, "commission_delivery_ht", ZERO) or ZERO
    logistic_margin = order.logistic_margin or ZERO

    # 2) Service FAGNI (HT)
    if prestation_total > ZERO:
        service_fee = (prestation_total * DEC("0.05"))
        if service_fee < DEC("500"):
            service_fee = DEC("500")
    else:
        service_fee = ZERO

    service_fee = service_fee.quantize(DEC("1."), rounding=ROUND_HALF_UP)
    order.service_fee = service_fee

    # 3) Revenu FAGNI HT
    fagni_ht = commission_laundry + commission_delivery + logistic_margin + service_fee
    fagni_ht = fagni_ht.quantize(DEC("1."), rounding=ROUND_HALF_UP)
    order.fagni_revenue_ht = fagni_ht

    # 4) TVA FAGNI (18%)
    vat_fagni = (fagni_ht * DEC("0.18")).quantize(DEC("1."), rounding=ROUND_HALF_UP)
    order.vat_fagni = vat_fagni

    # 5) Revenu FAGNI TTC
    order.fagni_revenue_ttc = fagni_ht + vat_fagni

    # 6) Total TTC facturé au client
    total_client_ttc = prestation_total + service_fee + delivery_fee + vat_fagni
    total_client_ttc = total_client_ttc.quantize(DEC("1."), rounding=ROUND_HALF_UP)
    order.total_client_ttc = total_client_ttc


@login_required
def portal_dashboard(request):
    """
    Dashboard central FAGNI (PORTAL) :
    - accès rapide aux stats commandes
    - liens vers OPS, finance, livreurs, etc.
    - tu pourras le rendre ultra premium visuellement dans orders/portal_dashboard.html
    """
    return render(request, "orders/portal_dashboard.html", {})


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
@login_required
def orders_list(request):
    """
    Liste des commandes FAGNI avec :
    - filtre par statut (all / pending / in_progress / done / canceled)
    - recherche plein texte (code, client, téléphone)
    - filtre par date de création (du / au)
    - pagination
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
        done_total=Sum("total_client_ttc", filter=Q(status="done")),
        pending_count=Count("id", filter=Q(status="pending")),
        in_progress_count=Count("id", filter=Q(status="in_progress")),
        done_count=Count("id", filter=Q(status="done")),
        canceled_count=Count("id", filter=Q(status="canceled")),
    )

    # --- Pagination ---
    page = request.GET.get("page") or 1
    paginator = Paginator(qs, 25)  # 25 commandes par page

    try:
        orders_page = paginator.page(page)
    except PageNotAnInteger:
        orders_page = paginator.page(1)
    except EmptyPage:
        orders_page = paginator.page(paginator.num_pages)

    context = {
        "orders": orders_page,
        "page_obj": orders_page,
        "paginator": paginator,
        "is_paginated": orders_page.has_other_pages(),

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
@login_required
def ops_dashboard(request):
    """
    Tableau de bord opérationnel FAGNI :
    - En attente
    - En cours
    - Terminées

    Important :
    - on affiche le TOTAL CLIENT (total_client_ttc)
    - on recalcule à l'affichage (sans save) pour éviter les incohérences
    - pagination indépendante par colonne (pending / in_progress / done)

    Lot 4.9:
    - Alertes SLA (commandes bloquées)
    - Alertes livreurs (inactive/offline)
    - drivers_json inclut updated_at + server_time

    Lot 4.9.2:
    - filtre OPS par livreur via ?driver_id=<id>
    """
    # ============================================================
    #  DRIVER FILTER (Lot 4.9.2)
    # ============================================================
    raw_driver_id = request.GET.get("driver_id")
    selected_driver = None
    selected_driver_id = None
    try:
        selected_driver_id = int(raw_driver_id) if raw_driver_id else None
    except (TypeError, ValueError):
        selected_driver_id = None

    if selected_driver_id:
        try:
            selected_driver = DeliveryPartner.objects.filter(is_active=True).get(pk=selected_driver_id)
        except DeliveryPartner.DoesNotExist:
            selected_driver = None
            selected_driver_id = None

    # ============================================================
    #  BASE QS (filtrable)
    # ============================================================
    base_qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .prefetch_related("items__photos")
        .order_by("-created_at")
    )

    # applique le filtre driver si présent
    if selected_driver_id:
        base_qs = base_qs.filter(delivery_partner_id=selected_driver_id)

    pending_qs = base_qs.filter(status="pending")
    in_progress_qs = base_qs.filter(status="in_progress")
    done_qs = base_qs.filter(status="done")

    # --------- Pagination (3 colonnes indépendantes) ----------
    per_page = int(request.GET.get("per_page", 12))

    pending_p = Paginator(pending_qs, per_page)
    progress_p = Paginator(in_progress_qs, per_page)
    done_p = Paginator(done_qs, per_page)

    pending_page_num = request.GET.get("pending_page", 1)
    progress_page_num = request.GET.get("progress_page", 1)
    done_page_num = request.GET.get("done_page", 1)

    try:
        pending_page_obj = pending_p.page(pending_page_num)
    except (PageNotAnInteger, EmptyPage):
        pending_page_obj = pending_p.page(1)

    try:
        progress_page_obj = progress_p.page(progress_page_num)
    except (PageNotAnInteger, EmptyPage):
        progress_page_obj = progress_p.page(1)

    try:
        done_page_obj = done_p.page(done_page_num)
    except (PageNotAnInteger, EmptyPage):
        done_page_obj = done_p.page(1)

    # --- Recompute display amounts (no save) for consistency ---
    def _refresh_amounts(page_obj):
        for o in page_obj.object_list:
            try:
                o.compute_totals(save=False)
            except Exception:
                pass

    _refresh_amounts(pending_page_obj)
    _refresh_amounts(progress_page_obj)
    _refresh_amounts(done_page_obj)

    # Total livré (global) — ici "global" = selon le filtre driver si actif
    try:
        DEC = DecimalField(max_digits=12, decimal_places=2)
        agg = done_qs.aggregate(
            prestation_sum=Coalesce(Sum(Cast("prestation_total", DEC)), Value(0, output_field=DEC)),
            total_sum=Coalesce(Sum(Cast("total", DEC)), Value(0, output_field=DEC)),
        )
        done_total = (agg["prestation_sum"] or Decimal("0"))
        if done_total <= 0:
            done_total = agg["total_sum"] or Decimal("0")
    except Exception:
        done_total = sum((o.prestation_total or o.total or Decimal("0")) for o in done_qs[:500])

    # ============================================================
    #  DRIVERS MAP (initial JSON) — on garde la carte globale
    # ============================================================
    today = timezone.localdate()
    start_week = today - timezone.timedelta(days=today.weekday())

    drivers_qs = DeliveryPartner.objects.filter(
        is_active=True,
        latitude__isnull=False,
        longitude__isnull=False,
    ).order_by("name")

    # Stats semaine (1 requête)
    stats_qs = (
        Order.objects.filter(
            delivery_partner__in=drivers_qs,
            created_at__date__gte=start_week,
            created_at__date__lte=today,
        )
        .values("delivery_partner_id")
        .annotate(
            week_orders=Count("id"),
            week_earnings=Coalesce(Sum("amount_driver_partner"), Decimal("0")),
        )
    )
    stats_map = {
        row["delivery_partner_id"]: {
            "week_orders": int(row["week_orders"] or 0),
            "week_earnings": int(row["week_earnings"] or 0),
        }
        for row in stats_qs
    }

    drivers = []
    for d in drivers_qs:
        try:
            lat = float(d.latitude)
            lng = float(d.longitude)
        except (TypeError, ValueError):
            continue

        st = stats_map.get(d.id, {"week_orders": 0, "week_earnings": 0})
        drivers.append({
            "id": d.id,
            "name": d.name,
            "city": getattr(d, "city", "") or "",
            "latitude": lat,
            "longitude": lng,
            "week_orders": st["week_orders"],
            "week_earnings": st["week_earnings"],
            "updated_at": d.updated_at.isoformat() if getattr(d, "updated_at", None) else None,
        })

    payload = {
        "ok": True,
        "count": len(drivers),
        "drivers": drivers,
        "server_time": timezone.now().isoformat(),
    }
    drivers_json = json.dumps(payload, ensure_ascii=False)

    # --------- helper querystring : conserver les params GET ----------
    def _qs_without(param_name: str) -> str:
        qd = request.GET.copy()
        if param_name in qd:
            qd.pop(param_name)
        s = qd.urlencode()
        return ("&" + s) if s else ""

    # reset url (retire driver_id + focus + highlight, garde le reste)
    qd_reset = request.GET.copy()
    for k in ["driver_id", "focus", "highlight", "pending_page", "progress_page", "done_page"]:
        if k in qd_reset:
            qd_reset.pop(k)
    reset_qs = qd_reset.urlencode()
    reset_url = reverse("orders:ops_dashboard")
    if reset_qs:
        reset_url = f"{reset_url}?{reset_qs}"

    highlight = request.GET.get("highlight")
    try:
        highlight_order_id = int(highlight) if highlight else None
    except (TypeError, ValueError):
        highlight_order_id = None

    # ============================================================
    #  Lot 4.9 — ALERTES (SLA) (filtrées si driver actif)
    # ============================================================
    now = timezone.now()

    SLA_PICKUP_H = 2        # pending -> pickup
    SLA_DROPOFF_H = 3       # pickup -> dropoff
    SLA_WASH_H = 48         # dropoff -> wash_done
    SLA_RETURN_H = 3        # wash_done -> return
    SLA_DELIVERED_H = 6     # return -> delivered

    alerts_orders = []
    scan_qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .filter(status__in=["pending", "in_progress"])
        .order_by("-created_at")
    )
    if selected_driver_id:
        scan_qs = scan_qs.filter(delivery_partner_id=selected_driver_id)

    scan_qs = scan_qs[:250]

    def _hours(dt_from, dt_to):
        if not dt_from or not dt_to:
            return None
        return (dt_to - dt_from).total_seconds() / 3600.0

    for o in scan_qs:
        reason = None
        age_h = None
        next_step = None

        if o.status == "pending" and not o.pickup_time:
            age_h = _hours(o.created_at, now) or 0
            if age_h >= SLA_PICKUP_H:
                reason = f"Collecte en retard (>{SLA_PICKUP_H}h)"
                next_step = "pickup"

        if o.status == "in_progress":
            if o.pickup_time and not o.dropoff_time:
                age_h = _hours(o.pickup_time, now) or 0
                if age_h >= SLA_DROPOFF_H:
                    reason = f"Dépôt blanchisserie en retard (>{SLA_DROPOFF_H}h après collecte)"
                    next_step = "dropoff"

            elif o.dropoff_time and not o.wash_complete_time:
                age_h = _hours(o.dropoff_time, now) or 0
                if age_h >= SLA_WASH_H:
                    reason = f"Lavage trop long (>{SLA_WASH_H}h après dépôt)"
                    next_step = "wash_done"

            elif o.wash_complete_time and not o.return_time:
                age_h = _hours(o.wash_complete_time, now) or 0
                if age_h >= SLA_RETURN_H:
                    reason = f"Reprise livreur en retard (>{SLA_RETURN_H}h après lavage)"
                    next_step = "return"

            elif o.return_time and not o.delivered_time:
                age_h = _hours(o.return_time, now) or 0
                if age_h >= SLA_DELIVERED_H:
                    reason = f"Livraison client en retard (>{SLA_DELIVERED_H}h après reprise)"
                    next_step = "delivered"

        if reason:
            alerts_orders.append({
                "order": o,
                "reason": reason,
                "age_h": round(age_h or 0, 1),
                "next_step": next_step,
            })

    # Alertes drivers (offline > 10min) — global
    ACTIVE_MS = 10 * 60  # 10 minutes en secondes
    drivers_offline = []
    for d in drivers_qs:
        if not getattr(d, "updated_at", None):
            drivers_offline.append({"driver": d, "age_min": None})
            continue
        delta = (now - d.updated_at).total_seconds()
        if delta > ACTIVE_MS:
            drivers_offline.append({"driver": d, "age_min": int(delta // 60)})

      # ============================================================
      #  DRIVERS LIST (dropdown filtre) — tous les drivers actifs
      # ============================================================
        drivers_list = DeliveryPartner.objects.filter(is_active=True).order_by("name")

        # Total affiché (selon filtre)
        displayed_total_count = base_qs.count()

    context = {
        # counts (selon filtre driver si actif)
        "pending_count": pending_qs.count(),
        "in_progress_count": in_progress_qs.count(),
        "done_count": done_qs.count(),

        "pending_page_obj": pending_page_obj,
        "progress_page_obj": progress_page_obj,
        "done_page_obj": done_page_obj,

        "pending_qs": _qs_without("pending_page"),
        "progress_qs": _qs_without("progress_page"),
        "done_qs": _qs_without("done_page"),

        "per_page": per_page,
        "done_total": done_total,

        # MAP
        "drivers_json": drivers_json,
        "drivers": drivers,

        "highlight_id": highlight_order_id,

        # FILTER UI
        "selected_driver": selected_driver,
        "selected_driver_id": selected_driver_id,
        "ops_reset_url": reset_url,

        # ALERTES
        "alerts_orders": alerts_orders[:20],
        "alerts_orders_count": len(alerts_orders),
        "drivers_offline": drivers_offline[:20],
        "drivers_offline_count": len(drivers_offline),

        # FILTRE DRIVER (Lot 4.9.2/4.9.3)
        "selected_driver": selected_driver,
        "selected_driver_id": selected_driver_id,
        "drivers_list": drivers_list,
        "reset_url": reset_url,
        "displayed_total_count": displayed_total_count,
    }
    return render(request, "orders/ops_dashboard.html", context)


@require_POST
def ops_update_step(request, order_id, action):
    """
    Met à jour les timestamps opérationnels :
    - pickup, dropoff, wash_done, return, delivered
    + bascule éventuellement le statut.

    Lot 4.8 :
    - Messages flash (success/warning/error)
    - Garde-fous : empêche les étapes dans le mauvais ordre
    - Redirect avec highlight (?highlight=<id>)
    """
    order = get_object_or_404(Order, pk=order_id)
    now = timezone.now()

    allowed = {"pickup", "dropoff", "wash_done", "return", "delivered"}
    if action not in allowed:
        messages.error(request, "Action OPS invalide.")
        return redirect("orders:ops_dashboard")

    mapping = {
        "pickup": ("pickup_time", "Collecte validée"),
        "dropoff": ("dropoff_time", "Dépôt blanchisserie validé"),
        "wash_done": ("wash_complete_time", "Lavage terminé validé"),
        "return": ("return_time", "Reprise livreur validée"),
        "delivered": ("delivered_time", "Livraison client validée"),
    }
    field_name, label = mapping[action]

    prerequisites = {
        "pickup": [],
        "dropoff": ["pickup_time"],
        "wash_done": ["dropoff_time"],
        "return": ["wash_complete_time"],
        "delivered": ["return_time"],
    }

    if getattr(order, field_name, None):
        messages.warning(request, f"Déjà fait : {label}.")
        return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")

    missing = [f for f in prerequisites[action] if not getattr(order, f, None)]
    if missing:
        messages.error(request, "Impossible : étape précédente non validée.")
        return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")

    setattr(order, field_name, now)

    if action == "pickup" and order.status == "pending":
        order.status = "in_progress"
    if action == "delivered":
        order.status = "done"

    try:
        order.compute_totals(save=False)
    except Exception:
        pass

    if action == "delivered":
        if (not getattr(order, "mlm_distributed", False)) and (order.service_fee or 0) > 0:
            _ = order.distribute_mlm_commissions

    order.save()
    messages.success(request, label)
    return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")


@login_required
def ops_drivers_live(request):
    """
    Lot 4.9 — JSON LIVE pour la carte Leaflet du OPS Dashboard.
    Retourne la liste des DeliveryPartner actifs avec latitude/longitude + stats semaine.
    + updated_at + server_time
    """
    today = timezone.localdate()
    start_week = today - timezone.timedelta(days=today.weekday())

    drivers_qs = DeliveryPartner.objects.filter(
        is_active=True,
        latitude__isnull=False,
        longitude__isnull=False,
    ).order_by("name")

    stats_qs = (
        Order.objects.filter(
            delivery_partner__in=drivers_qs,
            created_at__date__gte=start_week,
            created_at__date__lte=today,
        )
        .values("delivery_partner_id")
        .annotate(
            week_orders=Count("id"),
            week_earnings=Coalesce(Sum("amount_driver_partner"), Decimal("0")),
        )
    )
    stats_map = {
        row["delivery_partner_id"]: {
            "week_orders": int(row["week_orders"] or 0),
            "week_earnings": int(row["week_earnings"] or 0),
        }
        for row in stats_qs
    }

    drivers = []
    for d in drivers_qs:
        try:
            lat = float(d.latitude)
            lng = float(d.longitude)
        except (TypeError, ValueError):
            continue

        st = stats_map.get(d.id, {"week_orders": 0, "week_earnings": 0})
        drivers.append({
            "id": d.id,
            "name": d.name,
            "city": getattr(d, "city", "") or "",
            "latitude": lat,
            "longitude": lng,
            "week_orders": st["week_orders"],
            "week_earnings": st["week_earnings"],
            "updated_at": d.updated_at.isoformat() if getattr(d, "updated_at", None) else None,
        })

    return JsonResponse({
        "ok": True,
        "count": len(drivers),
        "drivers": drivers,
        "server_time": timezone.now().isoformat(),
    })


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



def _safe_dec(value):
    """
    Convertit en Decimal en gérant None / types bizarres.
    Renvoie Decimal('0') si la valeur n’est pas convertible.
    """
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _order_effective_total(order):
    """
    Montant réellement facturé au client pour une commande.
    On essaie dans l'ordre :
    - total_client_ttc (champ dédié si présent)
    - grand_total / total_ttc / total_ht / total
    - sinon on reconstruit : items + service + livraison
    """
    # 1) Champs agrégés
    for field in ["total_client_ttc", "grand_total", "total_ttc", "total_ht", "total"]:
        if hasattr(order, field):
            d = _safe_dec(getattr(order, field))
            if d > 0:
                return d

    # 2) Fallback : recomposer à partir des items + frais
    items_sum = Decimal("0")
    try:
        for it in order.items.all():
            line_total = getattr(it, "line_total", None)
            if line_total is not None:
                items_sum += _safe_dec(line_total)
            else:
                q = _safe_dec(getattr(it, "quantity", 0))
                p = _safe_dec(getattr(it, "unit_price", 0))
                items_sum += q * p
    except Exception:
        pass

    service_fee = _safe_dec(getattr(order, "service_fee", None))
    delivery_fee = _safe_dec(getattr(order, "delivery_fee", None))
    return items_sum + service_fee + delivery_fee


DEC_ZERO = Decimal("0")


def _dec_or_zero(val):
    """
    Convertit une valeur en Decimal de façon robuste.
    Retourne Decimal('0') si la conversion échoue.
    """
    if val is None:
        return DEC_ZERO
    if isinstance(val, Decimal):
        return val
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError, ValueError):
        return DEC_ZERO


def _compute_order_pricing(order):
    """
    Calcule tous les montants importants pour une commande FAGNI
    sans casser ce qui existe déjà en base.

    Retourne un dict avec :
      - items_total
      - service_fee
      - delivery_fee
      - total_client
      - driver_income
      - vat_fagni
      - fagni_revenue_ht
      - fagni_revenue_ttc
      - laundry_amount
      - logistic_margin
    """
    # 1) Sous-total prestations (à partir des items, sinon fallback sur prestation_total)
    items_total = DEC_ZERO
    try:
        items_qs = order.items.all()
    except Exception:
        items_qs = []

    for it in items_qs:
        line_total = _dec_or_zero(getattr(it, "total", None))
        if line_total > 0:
            items_total += line_total
            continue

        q = _dec_or_zero(getattr(it, "quantity", 0))
        unit = _dec_or_zero(getattr(it, "unit_price", 0))
        items_total += q * unit

    if items_total <= 0:
        items_total = _dec_or_zero(getattr(order, "prestation_total", 0))

    # 2) Frais connu directement sur la commande
    service_fee = _dec_or_zero(getattr(order, "service_fee", 0))
    delivery_fee = _dec_or_zero(getattr(order, "delivery_fee", 0))
    vat_fagni = _dec_or_zero(getattr(order, "vat_fagni", 0))
    total_client_ttc = _dec_or_zero(getattr(order, "total_client_ttc", 0))
    total_field = _dec_or_zero(getattr(order, "total", 0))

    # 3) Total TTC client (priorité à total_client_ttc si > 0)
    if total_client_ttc > 0:
        total_client = total_client_ttc
    elif total_field > 0:
        total_client = total_field
    else:
        # Reconstruction douce : prestations + frais + TVA FAGNI
        total_client = items_total + service_fee + delivery_fee + vat_fagni

    # 4) Revenu livreur
    driver_income = _dec_or_zero(getattr(order, "amount_driver_partner", None))
    if driver_income <= 0:
        driver_income = _dec_or_zero(getattr(order, "driver_logistic_cost", None))

    # 5) Marge logistique & revenus FAGNI
    logistic_margin = _dec_or_zero(getattr(order, "logistic_margin", None))
    fagni_revenue_ht = service_fee + logistic_margin

    # Si un champ fagni_revenue_ht existe et est déjà renseigné, on le respecte
    if hasattr(order, "fagni_revenue_ht"):
        db_fagni_ht = _dec_or_zero(getattr(order, "fagni_revenue_ht", 0))
        if db_fagni_ht > 0:
            fagni_revenue_ht = db_fagni_ht

    fagni_revenue_ttc = fagni_revenue_ht + vat_fagni

    # 6) Revenu blanchisserie (fallback simple)
    laundry_amount = _dec_or_zero(getattr(order, "amount_laundry_partner", None))
    if laundry_amount <= 0 and items_total > 0:
        # par défaut, on considère que la blanchisserie touche le montant prestations
        laundry_amount = items_total

    return {
        "items_total": items_total,
        "service_fee": service_fee,
        "delivery_fee": delivery_fee,
        "total_client": total_client,
        "driver_income": driver_income,
        "vat_fagni": vat_fagni,
        "fagni_revenue_ht": fagni_revenue_ht,
        "fagni_revenue_ttc": fagni_revenue_ttc,
        "laundry_amount": laundry_amount,
        "logistic_margin": logistic_margin,
    }


def _guess_delivery_fee(order, total_client=None, items_total=None, service_fee=None):
    """
    Essaie de deviner le montant de la livraison pour une commande.

    Logique :
    livraison ≈ total_client_ttc - prestations - service FAGNI - TVA FAGNI

    - total_client : TTC client (total_client_ttc, total ou annotation)
    - items_total : sous-total prestations (annotation ou recalcul)
    - service_fee : service FAGNI (order.service_fee)
    Utilise aussi order.vat_fagni si disponible.
    """
    # 1) TTC client
    total_client = _safe_dec(
        total_client
        or getattr(order, "total_client_ttc", None)
        or getattr(order, "total", None)
    )

    # 2) Sous-total prestations
    if items_total is None:
        items_total = _safe_dec(getattr(order, "items_total", None))
    else:
        items_total = _safe_dec(items_total)

    # 3) Service FAGNI et TVA FAGNI
    vat_fagni = _safe_dec(getattr(order, "vat_fagni", None))
    if service_fee is None:
        service_fee = _safe_dec(getattr(order, "service_fee", None))
    else:
        service_fee = _safe_dec(service_fee)

    if total_client <= 0:
        return Decimal("0")

    # livraison = TTC - prestations - service - TVA
    delivery_guess = total_client - items_total - service_fee - vat_fagni
    if delivery_guess > 0:
        return delivery_guess

    return Decimal("0")


def _order_driver_income(order, delivery_fee=None):
    """
    Calcule le revenu du livreur pour une commande.

    Priorité :
    1) order.amount_driver_partner si > 0
    2) order.driver_logistic_cost si > 0
    3) somme des DeliveryLeg.driver_amount si > 0
    4) delivery_fee - logistic_margin (après avoir deviné la livraison si nécessaire)
    Sinon, 0.
    """
    # 1) Montant partenaire livreur direct
    if hasattr(order, "amount_driver_partner") and order.amount_driver_partner is not None:
        d = _safe_dec(order.amount_driver_partner)
        if d > 0:
            return d

    # 2) Coût logistique payé au livreur
    if hasattr(order, "driver_logistic_cost") and order.driver_logistic_cost is not None:
        d = _safe_dec(order.driver_logistic_cost)
        if d > 0:
            return d

    # 3) Fallback : legs
    legs_rel = getattr(order, "legs", None)
    if legs_rel is not None:
        try:
            total_legs = Decimal("0")
            for leg in legs_rel.all():
                da = _safe_dec(getattr(leg, "driver_amount", None))
                total_legs += da
            if total_legs > 0:
                return total_legs
        except Exception:
            pass

    # 4) Gestion livraison
    if delivery_fee is None:
        delivery_fee = _safe_dec(getattr(order, "delivery_fee", None))
    else:
        delivery_fee = _safe_dec(delivery_fee)

    if delivery_fee <= 0:
        delivery_fee = _guess_delivery_fee(order)

    logistic_margin = _safe_dec(getattr(order, "logistic_margin", None))

    if delivery_fee > 0 and logistic_margin >= 0:
        candidate = delivery_fee - logistic_margin
        if candidate > 0:
            return candidate

    return Decimal("0")


@login_required
def finance_dashboard(request):
    """
    Dashboard financier FAGNI :
    - CA client
    - Montants à verser aux partenaires (blanchisserie + livreur)
    - Revenu FAGNI (TTC)
    - Marge logistique
    - Service fee total FAGNI
    """
    payment_filter = request.GET.get("payment", "all")
    qs = Order.objects.all()

    if payment_filter == "paid":
        qs = qs.filter(payment_status="paid")
    elif payment_filter == "unpaid":
        qs = qs.exclude(payment_status="paid")

    DEC = DecimalField(max_digits=12, decimal_places=2)

    qs = qs.annotate(
        total_client_expr=Coalesce(
            Cast(F("total_client_ttc"), DEC),
            Cast(F("prestation_total"), DEC),
            Cast(F("total"), DEC),
            Value(0, output_field=DEC),
        ),
        amount_laundry_safe=Coalesce(
            Cast(F("amount_laundry_partner"), DEC),
            Value(0, output_field=DEC),
        ),
        amount_driver_safe=Coalesce(
            Cast(F("amount_driver_partner"), DEC),
            Value(0, output_field=DEC),
        ),
        fagni_revenue_ttc_safe=(
            Coalesce(Cast(F("fagni_revenue_ht"), DEC), Value(0, output_field=DEC))
            + Coalesce(Cast(F("vat_fagni"), DEC), Value(0, output_field=DEC))
        ),
        logistic_margin_safe=Coalesce(
            Cast(F("logistic_margin"), DEC),
            Value(0, output_field=DEC),
        ),
        service_fee_safe=Coalesce(
            Cast(F("service_fee"), DEC),
            Value(0, output_field=DEC),
        ),
    ).annotate(
        partners_expr=Cast(F("amount_laundry_safe") + F("amount_driver_safe"), DEC)
    )

    agg = qs.aggregate(
        ca_client_total=Coalesce(Sum("total_client_expr", output_field=DEC), Value(0, output_field=DEC)),
        partners_laundry_total=Coalesce(Sum("amount_laundry_safe", output_field=DEC), Value(0, output_field=DEC)),
        partners_driver_total=Coalesce(Sum("amount_driver_safe", output_field=DEC), Value(0, output_field=DEC)),
        fagni_revenue_total=Coalesce(Sum("fagni_revenue_ttc_safe", output_field=DEC), Value(0, output_field=DEC)),
        logistic_margin_total=Coalesce(Sum("logistic_margin_safe", output_field=DEC), Value(0, output_field=DEC)),
        service_fee_total=Coalesce(Sum("service_fee_safe", output_field=DEC), Value(0, output_field=DEC)),
    )

    order_count = qs.count()
    ca_client_total = agg["ca_client_total"] or Decimal("0")

    avg_ticket = (ca_client_total / Decimal(order_count)).quantize(Decimal("0.01")) if order_count else Decimal("0.00")

    partners_laundry_total = agg["partners_laundry_total"] or Decimal("0")
    partners_driver_total = agg["partners_driver_total"] or Decimal("0")
    partners_total = partners_laundry_total + partners_driver_total

    context = {
        "payment_filter": payment_filter,
        "orders": qs.order_by("-created_at")[:50],

        "ca_client_total": ca_client_total,
        "order_count": order_count,
        "avg_ticket": avg_ticket,

        "partners_total": partners_total,
        "partners_laundry_total": partners_laundry_total,
        "partners_driver_total": partners_driver_total,

        "fagni_revenue_total": agg["fagni_revenue_total"],
        "logistic_margin_total": agg["logistic_margin_total"],
        "service_fee_total": agg["service_fee_total"],
    }

    return render(request, "orders/finance_dashboard.html", context)


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
#  LOGISTIQUE : VALIDATION COLLECTE / LIVRAISON (BLOC 1)
# ============================================================

# Créneaux "standards" qu'on utilise côté front (Bloc 3)
DEFAULT_SLOTS = [
    ("08:00-10:00", time(8, 0), time(10, 0)),
    ("10:00-12:00", time(10, 0), time(12, 0)),
    ("12:00-14:00", time(12, 0), time(14, 0)),
    ("14:00-16:00", time(14, 0), time(16, 0)),
    ("16:00-18:00", time(16, 0), time(18, 0)),
    ("18:00-20:00", time(18, 0), time(20, 0)),
]


def _find_slot(slot_value):
    """
    slot_value est censé être une string du type "08:00-10:00".
    On renvoie (start_time, end_time) ou (None, None) si introuvable.
    """
    if not slot_value:
        return None, None

    # On tolère quelques formats, mais on recommande "HH:MM-HH:MM"
    cleaned = slot_value.strip().replace(" ", "").replace("h", ":").replace("–", "-")
    # ex : "08h00 – 10h00" -> "08:00-10:00"

    for key, start_t, end_t in DEFAULT_SLOTS:
        key_clean = key.replace(" ", "")
        if cleaned == key_clean:
            return start_t, end_t

    # Dernier recours : essayer un split basique
    try:
        if "-" in cleaned:
            left, right = cleaned.split("-", 1)
            h1, m1 = left.split(":")
            h2, m2 = right.split(":")
            return time(int(h1), int(m1)), time(int(h2), int(m2))
    except Exception:
        pass

    return None, None


def _combine_date_and_time(date_str, time_obj):
    """
    Combine une date (YYYY-MM-DD) et un time en datetime aware.
    """
    if not date_str or not time_obj:
        return None

    try:
        d = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None

    naive_dt = datetime.combine(d, time_obj)
    return timezone.make_aware(naive_dt, timezone.get_current_timezone())


def compute_pickup_and_delivery_datetimes(post_data, config, now=None):
    """
    Calcule et VALIDE la logique collecte / livraison.

    Retourne :
    (pickup_mode, pickup_dt,
     delivery_mode, delivery_dt)

    - pickup_dt : datetime de début du créneau de collecte (ou "maintenant" si immédiate)
    - delivery_dt : datetime de début du créneau de livraison (ou None si non pertinent)
    """

    if now is None:
        now = timezone.localtime()

    # Sécurité si pas de config
    cutoff_hour = config.pickup_cutoff_hour if config else 10
    standard_sla_hours = config.standard_sla_hours if config else 48
    express_sla_hours = config.express_sla_hours if config else 24
    express_enabled = bool(config.express_enabled) if config else True
    scheduled_enabled = bool(config.scheduled_slots_enabled) if config else True

    pickup_mode = post_data.get("pickup_mode") or Order.PICKUP_MODE_NOW
    delivery_mode = post_data.get("delivery_mode") or Order.DELIVERY_MODE_STANDARD

    # -----------------------------
    # 1) COLLECTE
    # -----------------------------
    pickup_dt = None

    if pickup_mode == Order.PICKUP_MODE_NOW:
        # Collecte immédiate : on fixe la référence à maintenant
        pickup_dt = now

    elif pickup_mode == Order.PICKUP_MODE_LATER:
        pickup_date_str = post_data.get("pickup_date") or ""
        pickup_slot_value = post_data.get("pickup_slot") or ""

        if not pickup_date_str or not pickup_slot_value:
            raise ValidationError("Merci de choisir une date et un créneau horaire pour la collecte programmée.")

        start_t, end_t = _find_slot(pickup_slot_value)
        if not start_t or not end_t:
            raise ValidationError("Le créneau de collecte choisi est invalide.")

        pickup_dt = _combine_date_and_time(pickup_date_str, start_t)
        if not pickup_dt:
            raise ValidationError("La date de collecte programmée est invalide.")

        # Interdiction d'un créneau passé
        if pickup_dt <= now:
            raise ValidationError("Le créneau de collecte doit être dans le futur.")

    else:
        # Mode inconnu → on force sur NOW
        pickup_mode = Order.PICKUP_MODE_NOW
        pickup_dt = now

    # -----------------------------
    # 2) LIVRAISON
    # -----------------------------
    delivery_dt = None

    # --- Mode STANDARD (≈ 48h) ---
    if delivery_mode == Order.DELIVERY_MODE_STANDARD:
        # La livraison est calculée automatiquement : collecte + 48h
        delivery_dt = pickup_dt + timedelta(hours=standard_sla_hours)

    # --- Mode EXPRESS (≈ 24h) ---
    elif delivery_mode == Order.DELIVERY_MODE_EXPRESS:
        if not express_enabled:
            raise ValidationError("La livraison express n'est pas disponible pour le moment.")

        # Si la collecte est après l'heure limite, on bloque l'express
        if pickup_dt.hour >= cutoff_hour:
            raise ValidationError(
                f"Après {cutoff_hour}h, la formule express n'est plus garantie. "
                "Merci de choisir Standard ou Programmée."
            )

        delivery_dt = pickup_dt + timedelta(hours=express_sla_hours)

    # --- Mode PROGRAMMÉ ---
    elif delivery_mode == Order.DELIVERY_MODE_SCHEDULED:
        if not scheduled_enabled:
            raise ValidationError("La livraison programmée n'est pas disponible pour le moment.")

        delivery_date_str = post_data.get("delivery_date") or ""
        delivery_slot_value = post_data.get("delivery_slot") or ""

        if not delivery_date_str or not delivery_slot_value:
            raise ValidationError("Merci de choisir une date et un créneau horaire pour la livraison programmée.")

        d_start_t, d_end_t = _find_slot(delivery_slot_value)
        if not d_start_t or not d_end_t:
            raise ValidationError("Le créneau de livraison choisi est invalide.")

        delivery_dt = _combine_date_and_time(delivery_date_str, d_start_t)
        if not delivery_dt:
            raise ValidationError("La date de livraison programmée est invalide.")

        # Interdiction d'un créneau passé
        if delivery_dt <= now:
            raise ValidationError("Le créneau de livraison doit être dans le futur.")

        # Interdiction de livraison avant ou en même temps que la collecte
        if delivery_dt <= pickup_dt:
            raise ValidationError("La livraison ne peut pas être avant ou au même créneau que la collecte.")

    else:
        # Sécurité : si mode inconnu -> on revient à STANDARD
        delivery_mode = Order.DELIVERY_MODE_STANDARD
        delivery_dt = pickup_dt + timedelta(hours=standard_sla_hours)

    return pickup_mode, pickup_dt, delivery_mode, delivery_dt


def validate_delivery_mode(pickup_dt, delivery_dt, mode):
    if not pickup_dt or not delivery_dt:
        return None

    delta_hours = (delivery_dt - pickup_dt).total_seconds() / 3600

    # Express ⇢ ~24h
    if mode == "express":
        if not (12 <= delta_hours <= 36):
            return (
                "Le mode EXPRESS doit correspondre à une livraison environ 24h après la collecte."
            )

    # Standard ⇢ ~48h
    if mode == "standard":
        if not (36 <= delta_hours <= 72):
            return (
                "Le mode STANDARD doit correspondre à une livraison environ 48h après la collecte."
            )

    # Programmée ⇢ ≥ 72h
    if mode == "scheduled":
        if delta_hours < 72:
            return (
                "Le mode PROGRAMMÉ nécessite une livraison au moins 72h après la collecte. "
                "Pour une livraison avant 72h, choisis Standard (48h) ou Express (24h)."
            )

    return None


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
    - Assignation automatique d’un livreur si possible
    - Code parrain (referral_code)
    - Logistique : collecte / livraison validées (Bloc 1)
    """

    service_categories = ServiceCategory.objects.all()
    service_items = ServiceItem.objects.select_related("category").all()

    # Paramètres logistiques généraux (tarifs, etc.)
    logi = getattr(settings, "FAGNI_LOGISTICS", {})

    # Configuration logistique (SLA, cutoff, express, programmée…)
    config = LogisticsConfig.current()

    # --- Gestion du code parrain initial (GET ou POST) ---
    if request.method == "POST":
        referral_initial = (request.POST.get("referral_code") or "").strip()
    else:
        referral_initial = (
            (request.GET.get("ref") or "").strip()
            or (request.GET.get("aff") or "").strip()
        )

    # --- Valeurs par défaut / re-remplissage pour la collecte & la livraison ---
    if request.method == "POST":
        pickup_mode_val = request.POST.get("pickup_mode") or Order.PICKUP_MODE_NOW
        pickup_date_val = request.POST.get("pickup_date") or ""
        pickup_slot_val = request.POST.get("pickup_slot") or ""

        delivery_mode_val = request.POST.get("delivery_mode") or Order.DELIVERY_MODE_STANDARD
        delivery_date_val = request.POST.get("delivery_date") or ""
        delivery_slot_val = request.POST.get("delivery_slot") or ""
    else:
        pickup_mode_val = Order.PICKUP_MODE_NOW
        pickup_date_val = ""
        pickup_slot_val = ""

        delivery_mode_val = Order.DELIVERY_MODE_STANDARD
        delivery_date_val = ""
        delivery_slot_val = ""

    # Contexte de base (utilisé aussi en cas d’erreur POST)
    context = {
        "service_categories": service_categories,
        "service_items": service_items,
        "error": None,
        "client_phone": request.POST.get("client_phone", "") if request.method == "POST" else "",
        "client_name": request.POST.get("client_name", "") if request.method == "POST" else "",
        "client_address": request.POST.get("client_address", "") if request.method == "POST" else "",
        "client_lat": request.POST.get("client_lat", "") if request.method == "POST" else "",
        "client_lng": request.POST.get("client_lng", "") if request.method == "POST" else "",
        "delivery_address": request.POST.get("delivery_address", "") if request.method == "POST" else "",
        "referral_code": referral_initial,
        "order_notes": request.POST.get("order_notes", "") if request.method == "POST" else "",
        "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
        "delivery_min_fee": logi.get("client_min_fee", 1000),
        "delivery_price_per_km": logi.get("client_price_per_km", 150),
        "delivery_fixed_fee": logi.get("client_fixed_fee", 300),
        # Infos config logistique (pour affichage dans le template si besoin)
        "logi_pickup_cutoff_hour": config.pickup_cutoff_hour if config else 10,
        "logi_standard_sla_hours": config.standard_sla_hours if config else 48,
        "logi_express_sla_hours": config.express_sla_hours if config else 24,
        "logi_express_enabled": bool(config.express_enabled) if config else True,
        "logi_express_extra_flat": config.express_extra_flat if config else 0,
        "logi_express_extra_percent": config.express_extra_percent if config else 0,
        "logi_scheduled_enabled": bool(config.scheduled_slots_enabled) if config else True,
        "pickup_mode": pickup_mode_val,
        "pickup_date": pickup_date_val,
        "pickup_slot": pickup_slot_val,
        "delivery_mode": delivery_mode_val,
        "delivery_date": delivery_date_val,
        "delivery_slot": delivery_slot_val,
    }

    context["drivers"] = get_active_drivers()

    # --- GET : on affiche le formulaire ---
    if request.method != "POST":
        return render(request, "orders/create.html", context)

    # --- POST : on traite la commande ---
    phone = (request.POST.get("client_phone") or "").strip()
    name = (request.POST.get("client_name") or "").strip()
    address = (request.POST.get("client_address") or "").strip()

    lat_raw = (request.POST.get("client_lat") or "").strip()
    lng_raw = (request.POST.get("client_lng") or "").strip()

    delivery_address_input = (request.POST.get("delivery_address") or "").strip()
    referral_code = referral_initial
    notes = (request.POST.get("order_notes") or "").strip()

    # 1) Validation minimale client
    if not phone or not name:
        context["error"] = "Merci de renseigner au moins le nom et le téléphone du client."
        return render(request, "orders/create.html", context)

    # 2) Création / mise à jour du client
    try:
        customer, created = Customer.objects.get_or_create(
            phone=phone,
            defaults={
                "name": name,
                "address": address,
            },
        )
    except Customer.MultipleObjectsReturned:
        customer = (
            Customer.objects.filter(phone=phone)
            .order_by("-id")
            .first()
        )
        created = False

    changed = False
    if name and customer.name != name:
        customer.name = name
        changed = True
    if address and customer.address != address:
        customer.address = address
        changed = True

    # latitude / longitude si présentes
    try:
        if lat_raw:
            customer.latitude = Decimal(lat_raw)
            changed = True
        if lng_raw:
            customer.longitude = Decimal(lng_raw)
            changed = True
    except Exception:
        # On ignore les erreurs de parsing lat/lng
        pass

    if changed:
        customer.save()

    # 3) Création de la commande (vide au début, pour avoir un ID)
    code = uuid.uuid4().hex[:8].upper()
    while Order.objects.filter(code=code).exists():
        code = uuid.uuid4().hex[:8].upper()

    order = Order.objects.create(
        customer=customer,
        status="pending",
        code=code,
        referral_code=referral_code or None,
        notes=notes,
    )

    # 4) Adresses collecte / livraison + géolocalisation
    order.pickup_address = address or customer.address or ""

    if delivery_address_input:
        order.delivery_address = delivery_address_input
    else:
        # Si rien saisi, livraison = collecte
        order.delivery_address = order.pickup_address

    try:
        order.pickup_lat = float(lat_raw) if lat_raw else None
    except (TypeError, ValueError):
        order.pickup_lat = None

    try:
        order.pickup_lng = float(lng_raw) if lng_raw else None
    except (TypeError, ValueError):
        order.pickup_lng = None

    # Pour l’instant, si aucune lat/lng spécifique pour la livraison :
    # - si adresse livraison = collecte → on copie
    delivery_lat_raw = request.POST.get("delivery_lat") or ""
    delivery_lng_raw = request.POST.get("delivery_lng") or ""

    if not delivery_address_input:
        order.delivery_lat = order.pickup_lat
        order.delivery_lng = order.pickup_lng
    else:
        try:
            order.delivery_lat = float(delivery_lat_raw) if delivery_lat_raw else None
        except (TypeError, ValueError):
            order.delivery_lat = None
        try:
            order.delivery_lng = float(delivery_lng_raw) if delivery_lng_raw else None
        except (TypeError, ValueError):
            order.delivery_lng = None

    # 5) Logistique : collecte & livraison (BLOC 1)
    try:
        pickup_mode, pickup_dt, delivery_mode, delivery_dt = compute_pickup_and_delivery_datetimes(
            request.POST, config
        )
    except ValidationError as e:
        # On supprime la commande vide, on remonte l’erreur à l’UI
        order.delete()
        context["error"] = str(e)
        return render(request, "orders/create.html", context)

    order.pickup_mode = pickup_mode
    order.delivery_mode = delivery_mode

    # Si collecte programmée → on stocke date & heure de début
    if pickup_mode == Order.PICKUP_MODE_LATER and pickup_dt:
        order.pickup_scheduled_date = pickup_dt.date()
        order.pickup_scheduled_time = pickup_dt.time()
    else:
        order.pickup_scheduled_date = None
        order.pickup_scheduled_time = None

    # Si livraison programmée → on stocke date & heure de début
    if delivery_mode == Order.DELIVERY_MODE_SCHEDULED and delivery_dt:
        order.delivery_scheduled_date = delivery_dt.date()
        order.delivery_scheduled_time = delivery_dt.time()
    else:
        order.delivery_scheduled_date = None
        order.delivery_scheduled_time = None

    error = validate_delivery_mode(pickup_dt, delivery_dt, delivery_mode)
    if error:
        context["error"] = error
        return render(request, "orders/create.html", context)

    order.save()

    # ---------------------------------------------------------
    # Création / garantie des tronçons logistiques (DeliveryLeg)
    # ---------------------------------------------------------
    try:
        ensure_delivery_legs_for_order(order)
    except Exception as e:
        # On loggue, mais on ne bloque pas la création de la commande
        print("Erreur ensure_delivery_legs_for_order:", e)

    ensure_order_geocoded(order, save=True)

    # 6) Assignation automatique d’une blanchisserie
    laundry = assign_best_laundry(customer)
    if laundry:
        order.laundry_partner = laundry

    # 7) Assignation automatique d’un livreur
    driver = assign_best_driver(customer.latitude, customer.longitude)
    if driver:
        order.delivery_partner = driver
        order.delivery_partner_unassigned_reason = None
    else:
        order.delivery_partner_unassigned_reason = (
            "Aucun livreur disponible au moment de la commande."
        )

    # 8) Calcul automatique des frais de livraison
    try:
        delivery_fee = order.compute_delivery_fee()
    except Exception:
        logi = getattr(settings, "FAGNI_LOGISTICS", {})
        delivery_fee = Decimal(str(logi.get("client_min_fee", 1000)))

    order.delivery_fee = delivery_fee

    # 9) Lignes de commande
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

        try:
            qty = int(qty_str)
        except Exception:
            qty = 0

        try:
            pu = Decimal(pu_str)
        except Exception:
            pu = Decimal("0")

        if qty <= 0 or pu <= 0:
            continue

        try:
            service_obj = ServiceItem.objects.get(pk=sid)
        except ServiceItem.DoesNotExist:
            service_obj = None

        line_total = (pu * qty).quantize(Decimal("0.01"))

        item = OrderItem.objects.create(
            order=order,
            service=service_obj,
            designation=desc,
            quantity=qty,
            unit_price=pu,
            total=line_total,
        )
        created_any_item = True

        file_field_name = f"photos_{idx}"
        for photo_file in request.FILES.getlist(file_field_name):
            if not photo_file:
                continue
            OrderItemPhoto.objects.create(
                order_item=item,
                image=photo_file,
            )

    if not created_any_item:
        order.delete()
        context["error"] = "Ajoute au moins une ligne de prestation à la commande."
        return render(request, "orders/create.html", context)

    # 10) Recalcul financier complet (nouveau moteur pricing)
    recompute_order_financials(order)
    apply_fagni_pricing(order)
    order.recompute_distances_from_positions()
    order.save()

    return redirect("orders:detail", order_id=order.id)


def get_active_drivers():
    return DeliveryPartner.objects.filter(is_active=True)


def assign_best_driver(lat, lng):
    """
    Assigne automatiquement un livreur 'intelligent' :

    - On filtre les livreurs actifs avec coordonnées.
    - On calcule la distance client ↔ livreur.
    - On regarde la charge de travail (nb de commandes non terminées).
    - On choisit d'abord celui qui a le moins de commandes actives,
      puis le plus proche en distance.
    """
    if not lat or not lng:
        return None

    # Livreur actifs avec latitude / longitude
    drivers = DeliveryPartner.objects.filter(
        is_active=True,
        latitude__isnull=False,
        longitude__isnull=False,
    )

    if not drivers.exists():
        return None

    best_driver = None
    best_key = None  # (nb_commandes_actives, distance_km)

    for d in drivers:
        d_lat = d.latitude
        d_lng = d.longitude
        if not d_lat or not d_lng:
            continue

        # Distance en km
        dist = haversine_distance_km(lat, lng, d_lat, d_lng)
        if dist is None:
            continue

        # Charge de travail = nb de commandes non terminées / non annulées
        active_orders = d.orders.exclude(status__in=["done", "canceled"]).count()

        current_key = (active_orders, dist)

        if best_key is None or current_key < best_key:
            best_key = current_key
            best_driver = d

    return best_driver


def assign_best_laundry(customer):
    """
    Assigne automatiquement la blanchisserie 'intelligente' :

    - On utilise la position du client.
    - On filtre les blanchisseries actives avec coordonnées.
    - On calcule la distance client ↔ blanchisserie.
    - On regarde la charge de travail (nb de commandes non terminées).
    - On choisit d'abord celle qui a le moins de commandes actives,
      puis la plus proche.
    """
    if not customer or not customer.latitude or not customer.longitude:
        return None

    origin_lat = customer.latitude
    origin_lng = customer.longitude

    laundries = LaundryPartner.objects.filter(
        is_active=True,
        latitude__isnull=False,
        longitude__isnull=False,
    )

    if not laundries.exists():
        return None

    best_laundry = None
    best_key = None  # (nb_commandes_actives, distance_km)

    for l in laundries:
        dest_lat = getattr(l, "latitude", None)
        dest_lng = getattr(l, "longitude", None)
        if not dest_lat or not dest_lng:
            continue

        dist = haversine_distance_km(origin_lat, origin_lng, dest_lat, dest_lng)
        if dist is None:
            continue

        active_orders = l.orders.exclude(status__in=["done", "canceled"]).count()
        current_key = (active_orders, dist)

        if best_key is None or current_key < best_key:
            best_key = current_key
            best_laundry = l

    return best_laundry


def build_order_finance_context(order):
    """
    Construit le contexte financier FAGNI pour l'écran de détail commande,
    en s'appuyant sur le moteur compute_order_financials(order).

    Retourne un dict utilisé par le template detail.html pour afficher :
    - Sous-total prestations
    - Service FAGNI
    - Frais de livraison client
    - TVA FAGNI
    - Total TTC client
    - Montant dû blanchisserie / livreur
    - Total partenaires
    - Revenu FAGNI
    - Marge logistique / livraison
    """

    from .finance import compute_order_financials  # import local pour éviter tout import circulaire

    data = compute_order_financials(order)

    # Sécurisation des valeurs (par défaut 0)
    prestation_total = data.get("prestation_total", Decimal("0"))
    service_fee_ht = data.get("service_fee_ht", Decimal("0"))
    delivery_fee_client = data.get("delivery_fee_client", Decimal("0"))
    vat_fagni = data.get("vat_fagni", Decimal("0"))
    total_ttc_client = data.get("total_ttc_client", Decimal("0"))

    amount_laundry = data.get("commission_laundry_ht", Decimal("0"))
    amount_driver = data.get("commission_delivery_ht", Decimal("0"))
    partners_total = amount_laundry + amount_driver

    fagni_revenue_ht = data.get("fagni_revenue_ht", Decimal("0"))
    margin_delivery = data.get("margin_delivery", Decimal("0"))
    express_surcharge = data.get("express_surcharge", Decimal("0"))

    return {
        "order": order,

        # Montants "client"
        "prestation_total": prestation_total,
        "service_fee_ht": service_fee_ht,
        "delivery_fee_client": delivery_fee_client,
        "vat_fagni": vat_fagni,
        "total_ttc_client": total_ttc_client,

        # Partenaires
        "amount_laundry": amount_laundry,
        "amount_driver": amount_driver,
        "partners_total": partners_total,

        # FAGNI
        "fagni_revenue_ht": fagni_revenue_ht,
        "margin_delivery": margin_delivery,
        "express_surcharge": express_surcharge,
    }


# ============================================================
#   DETAIL COMMANDE
# ============================================================
def detail(request, order_id):
    """
    Détail d'une commande FAGNI :
    - recalcule si besoin les frais de livraison via le moteur intégré de la commande
    - applique le modèle financier FAGNI via update_financials()
    - affiche les lignes, photos et historique de statut
    """
    # --- Récupération de la commande ---
    order = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .filter(pk=order_id)
        .first()
    )

    if not order:
        return redirect("orders:list")

    # --- Lignes de commande (prestations) ---
    items = (
        OrderItem.objects
        .filter(order=order)
        .select_related("service", "service__category")
        .prefetch_related("photos")
    )

    # ====================================================================
    # 1) FRAIS DE LIVRAISON : si 0 ou null, on utilise le moteur interne
    #    de la commande (compute_delivery_fee)
    # ====================================================================
    needs_delivery_recompute = (
        (not order.delivery_fee or Decimal(str(order.delivery_fee)) == Decimal("0"))
        and order.customer
        and order.laundry_partner
    )

    if needs_delivery_recompute:
        # Utilise le moteur dynamique (distance + surge éventuel)
        delivery_fee = order.compute_delivery_fee()

        # On met à jour la commande avec ce qui a été calculé dans compute_delivery_fee
        order.delivery_fee = delivery_fee
        # compute_delivery_fee a déjà mis à jour :
        # - order.distance_km
        # - order.driver_logistic_cost
        # - order.logistic_margin
        order.save(
            update_fields=[
                "delivery_fee",
                "distance_km",
                "driver_logistic_cost",
                "logistic_margin",
            ]
        )
    else:
        # On normalise juste le type Decimal si une valeur existe déjà
        if order.delivery_fee is not None:
            order.delivery_fee = Decimal(str(order.delivery_fee))

    # ====================================================================
    # 2) APPLICATION DU MODÈLE FAGNI (update_financials)
    # ====================================================================
    # -> ceci remplit notamment :
    #    - prestation_total
    #    - service_fee
    #    - commission_laundry_ht / commission_delivery_ht
    #    - fagni_revenue_ht / fagni_revenue_ttc
    #    - vat_fagni
    #    - amount_laundry_partner / amount_driver_partner
    #    - total_client_ttc
    data = order.update_financials(save=True)

    # ====================================================================
    # 3) PHOTOS & HISTORIQUE
    # ====================================================================
    all_photos = OrderItemPhoto.objects.filter(order_item__order=order)

    status_history = (
        OrderStatusHistory.objects
        .filter(order=order)
        .order_by("changed_at")
    )

    ticket_url = reverse("orders:order_ticket_pdf", args=[order.id])
    ticket_thermal_url = reverse("orders:order_ticket_thermal_pdf", args=[order.id])

    # Contexte "finance" déjà utilisé dans le template (avec des fallback
    # sur les champs order.* si besoin)
    finance = build_order_finance_context(order)

    context = {
        "order": order,
        "items": items,
        "status_history": status_history,
        "all_photos": all_photos,
        "ticket_url": ticket_url,
        "ticket_thermal_url": ticket_thermal_url,
        "finance": finance,
        "financial_data": data,  # si un jour tu veux exploiter directement le dict
    }

    return render(request, "orders/detail.html", context)


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
    order.update_financials()
    recompute_order_financials(order)  # 🔥 applique les règles FAGNI
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


def _q(amount: Decimal) -> Decimal:
    # arrondi FCFA à l'unité (pas de centimes)
    return Decimal(amount or 0).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _get_vat_rate_percent(cfg) -> Decimal:
    # On essaie de lire un éventuel champ admin.
    # Sinon fallback à 18% (CI).
    for attr in ("vat_percent", "vat_rate", "vat_fagni_percent", "tva_percent"):
        v = getattr(cfg, attr, None)
        if v is not None:
            try:
                return Decimal(str(v))
            except Exception:
                pass
    return Decimal("18")


def _build_invoice_context(order):
    cfg = get_pricing_settings()
    amounts = compute_order_amounts(order)

    vat_rate = _get_vat_rate_percent(cfg)

    # Total HT client = prestations + livraison + express (si facturé client) + service FAGNI
    total_ht_client = _q(
        _q(amounts.get("subtotal", 0))
        + _q(amounts.get("delivery_fee_client", 0))
        + _q(amounts.get("express_for_client", 0))
        + _q(amounts.get("service_fee_ht", 0))
    )

    # TVA calculée sur revenus FAGNI (HT)
    fagni_revenue_ht = _q(amounts.get("fagni_revenue_ht", 0))
    vat_amount = _q((fagni_revenue_ht * vat_rate) / Decimal("100"))

    total_ttc_client = _q(total_ht_client + vat_amount)

    # Compat anciennes variables (si d'autres templates les utilisent)
    base_ht = total_ht_client
    grand_total = total_ttc_client

    return {
        "cfg": cfg,
        "amounts": amounts,
        "vat_rate": vat_rate,
        "vat_amount": vat_amount,
        "total_ht_client": total_ht_client,
        "total_ttc_client": total_ttc_client,
        "base_ht": base_ht,
        "tva_amount": vat_amount,
        "grand_total": grand_total,
    }


@login_required
def order_ticket_pdf(request, order_id):
    """
    Ticket PDF (HTML rendu).
    Source de vérité = compute_order_amounts(order) (pricing.py).
    """
    order = (
        Order.objects
        .filter(pk=order_id)
        .select_related("customer", "laundry_partner", "delivery_partner")
        .prefetch_related("items__service", "items__photos")
        .first()
    )
    if not order:
        return redirect("orders:list")

    items = order.items.all()

    detail_url = request.build_absolute_uri(reverse("orders:detail", args=[order.id]))
    qr_data = detail_url

    context = {
        "order": order,
        "items": items,
        "qr_data": qr_data,
        **_build_invoice_context(order),
        "invoice_settings": get_invoice_settings_clean(),
    }
    return render(request, "orders/ticket_pdf.html", context)


@login_required
def order_invoice_pdf(request, order_id):
    """
    Génère une FACTURE PDF A4 (WeasyPrint) :
    - accessible uniquement si commande payée
    - montants alignés sur le ticket (source unique = _build_invoice_context)
    - header/footer pilotés par InvoiceSettings (admin)
    """
    order = get_object_or_404(
        Order.objects.select_related("customer", "laundry_partner", "delivery_partner"),
        pk=order_id
    )

    # Sécurité : facture uniquement si payé
    if order.payment_status != "paid":
        return HttpResponseForbidden("Facture indisponible : commande non payée.")

    # S'assurer que les montants + invoice_number sont à jour (et persistés)
    try:
        order.update_financials(save=True)
    except Exception:
        pass

    items = order.items.all().order_by("id")

    # ✅ Source unique (comme ticket_pdf / ticket_thermal)
    ctx = _build_invoice_context(order)

    # ✅ Header/Footer admin (nettoyé)
    invoice_settings = get_invoice_settings_clean()
    if invoice_settings:
        ctx["invoice_settings"] = invoice_settings

    context = {
        "order": order,
        "items": items,
        "now": timezone.now(),
        **ctx,
    }

    html_string = render_to_string("orders/invoice_pdf.html", context=context, request=request)
    pdf = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()

    filename = f"FACTURE-{order.invoice_number or order.code or order.id}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{smart_str(filename)}"'
    return response


@login_required
def order_ticket_thermal_pdf(request, order_id):
    """
    Ticket thermique (80mm).
    Source de vérité = compute_order_amounts(order) (pricing.py).
    """
    order = (
        Order.objects
        .filter(pk=order_id)
        .select_related("customer", "laundry_partner", "delivery_partner")
        .prefetch_related("items__service", "items__photos")
        .first()
    )
    if not order:
        return redirect("orders:list")

    items = order.items.all()

    ctx_amounts = _build_invoice_context(order)

    invoice_settings = get_invoice_settings_clean()
    if invoice_settings:
        ctx_amounts["invoice_settings"] = invoice_settings

    # Total à mettre dans le QR (priorité : total_ttc_client sinon grand_total)
    qr_total = ctx_amounts.get("total_ttc_client") or ctx_amounts.get("grand_total") or Decimal("0")

    customer_phone = ""
    if getattr(order, "customer", None) and getattr(order.customer, "phone", None):
        customer_phone = order.customer.phone

    try:
        qr_total = int(Decimal(qr_total))
    except Exception:
        qr_total = 0

    qr_data = f"CMD:{order.code}|TEL:{customer_phone}|TOTAL:{qr_total}"

    context = {
        "order": order,
        "items": items,
        "qr_data": qr_data,
        **ctx_amounts,
    }
    return render(request, "orders/ticket_thermal_pdf.html", context)


def safe_decimal(value, default=Decimal("0")):
    try:
        if value in (None, ""):
            return default
        return Decimal(str(value))
    except Exception:
        return default


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

    # 🔒 HARD LOCK : une commande payée ne doit plus être modifiable (GET + POST)
    if getattr(order, "payment_status", None) == "paid":
        messages.error(request, "Commande payée : modification interdite.")
        return redirect("orders:detail", order_id=order.id)

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

            # Ancienne ligne sans service_id -> on garde (compat vieilles commandes)
            if not raw_service_id and existing_item:
                kept_ids.add(existing_item.id)
                if raw_index != "":
                    files = request.FILES.getlist(f"photos_{raw_index}")
                    for f in files:
                        OrderItemPhoto.objects.create(order_item=existing_item, image=f)
                continue

            if not raw_service_id:
                continue

            # quantité
            try:
                quantity = int(raw_quantity)
            except (TypeError, ValueError):
                quantity = 0

            # prix unitaire Decimal
            clean_unit_price = (raw_unit_price or "").replace(" ", "").replace("\u00a0", "")
            try:
                unit_price = Decimal(clean_unit_price)
            except (TypeError, ValueError, InvalidOperation):
                unit_price = Decimal("0")

            # Ligne invalide (nouvelle) : on n’en crée pas
            if quantity <= 0 or unit_price <= 0:
                if existing_item:
                    kept_ids.add(existing_item.id)
                    if raw_index != "":
                        files = request.FILES.getlist(f"photos_{raw_index}")
                        for f in files:
                            OrderItemPhoto.objects.create(order_item=existing_item, image=f)
                continue

            designation = raw_designation

            # Ligne existante ou nouvelle ?
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

            # Ajout de nouvelles photos
            if raw_index != "":
                files = request.FILES.getlist(f"photos_{raw_index}")
                for f in files:
                    OrderItemPhoto.objects.create(order_item=item, image=f)

        # --- 3) Suppression des lignes qui ne sont plus envoyées ---
        for it in existing_items_qs:
            if it.id not in kept_ids:
                it.delete()

        # --- 4) Recalcul des totaux ---
        if hasattr(order, "recalculate_totals"):
            order.recalculate_totals()

        order.recompute_distances_from_positions()
        order.compute_totals(save=True)
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
def driver_performance_me(request):
    """
    Redirige le livreur connecté vers sa page de performance,
    en se basant sur son email (DeliveryPartner.email).
    """
    user = request.user
    email = (user.email or "").strip()

    from partners.models import DeliveryPartner  # import local pour éviter les cycles

    driver = None
    if email:
        try:
            driver = DeliveryPartner.objects.get(email__iexact=email)
        except DeliveryPartner.DoesNotExist:
            driver = None

    if not driver:
        # Pas de profil livreur lié → retour au hub
        return redirect("orders:driver_hub")

    # Redirection vers la vue existante qui prend un driver_id
    return redirect("orders:driver_performance", driver_id=driver.id)


@login_required
def driver_performance(request, driver_id):
    """
    Dashboard avancé de performance pour un livreur :
    - KPIs (commandes, distance, revenu, taux de complétion, etc.)
    - Séries journalières pour graphiques (Chart.js)
    - Classement parmi les autres livreurs
    """
    # 1) Récupération du livreur
    driver = get_object_or_404(DeliveryPartner, pk=driver_id)

    # 2) Filtre de période (7 / 30 / 90 / 180 jours)
    period = request.GET.get("period", "30")
    try:
        days = int(period)
    except (TypeError, ValueError):
        days = 30

    if days not in (7, 30, 90, 180):
        days = 30

    end = timezone.now()
    start = end - datetime.timedelta(days=days)

    # 3) Query de base : toutes les commandes du livreur sur la période
    orders_qs = (
        Order.objects.filter(
            delivery_partner=driver,
            created_at__gte=start,
            created_at__lte=end,
        )
        .select_related("customer")
        .order_by("-created_at")
    )

    # 4) KPIs globaux
    total_orders = orders_qs.count()
    done_orders = orders_qs.filter(status="done").count()
    in_progress_orders = orders_qs.filter(status="in_progress").count()
    pending_orders = orders_qs.filter(status="pending").count()
    canceled_orders = orders_qs.filter(status="canceled").count()

    aggregates = orders_qs.aggregate(
        total_distance_km=Sum("distance_km"),
        total_income=Sum("driver_logistic_cost"),
    )
    total_distance_km = aggregates["total_distance_km"] or 0
    total_income = aggregates["total_income"] or 0

    # Taux de complétion / annulation
    completion_rate = 0
    cancel_rate = 0
    if total_orders > 0:
        completion_rate = round(done_orders * 100 / total_orders, 1)
        cancel_rate = round(canceled_orders * 100 / total_orders, 1)

    # Moyennes
    avg_income_per_order = 0
    avg_income_per_km = 0
    if done_orders > 0:
        avg_income_per_order = round(total_income / done_orders, 2)
    if total_distance_km:
        avg_income_per_km = round(total_income / total_distance_km, 2)

    # 5) Statistiques journalières (séries pour graphiques)
    daily_stats_qs = (
        orders_qs
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(
            orders_count=Count("id"),
            day_income=Sum("driver_logistic_cost"),
            day_distance=Sum("distance_km"),
        )
        .order_by("day")
    )

    daily_labels = []
    daily_orders_series = []
    daily_income_series = []
    daily_distance_series = []

    for row in daily_stats_qs:
        day = row["day"]
        daily_labels.append(day.strftime("%d/%m"))
        daily_orders_series.append(row["orders_count"] or 0)
        daily_income_series.append(float(row["day_income"] or 0))
        daily_distance_series.append(float(row["day_distance"] or 0))

    # 6) Classement des livreurs (leaderboard)
    leaderboard_qs = (
        Order.objects.filter(
            created_at__gte=start,
            created_at__lte=end,
            status="done",
            delivery_partner__isnull=False,
        )
        .values("delivery_partner_id", "delivery_partner__name")
        .annotate(
            total_income=Sum("driver_logistic_cost"),
            total_orders=Count("id"),
            total_distance=Sum("distance_km"),
        )
        .order_by("-total_income")
    )

    leaderboard = list(leaderboard_qs[:10])
    current_rank = None
    for idx, row in enumerate(leaderboard_qs, start=1):
        if row["delivery_partner_id"] == driver.id:
            current_rank = idx
            break

    # On ne garde que les 10 premiers pour l'affichage
    leaderboard = leaderboard[:10]

    # 7) Quelques dernières commandes pour le bas de page
    last_orders = orders_qs[:10]

    context = {
        "driver": driver,
        "period_days": days,
        "start_date": start,
        "end_date": end,

        # KPIs
        "total_orders": total_orders,
        "done_orders": done_orders,
        "in_progress_orders": in_progress_orders,
        "pending_orders": pending_orders,
        "canceled_orders": canceled_orders,
        "total_distance_km": total_distance_km,
        "total_income": total_income,
        "completion_rate": completion_rate,
        "cancel_rate": cancel_rate,
        "avg_income_per_order": avg_income_per_order,
        "avg_income_per_km": avg_income_per_km,

        # Séries pour les graphiques
        "daily_labels": daily_labels,
        "daily_orders_series": daily_orders_series,
        "daily_income_series": daily_income_series,
        "daily_distance_series": daily_distance_series,

        # Classement
        "leaderboard": leaderboard,
        "current_rank": current_rank,

        # Dernières commandes
        "last_orders": last_orders,
    }

    return render(request, "orders/driver_performance.html", context)


@login_required
def driver_wallet(request):
    """
    Dashboard du wallet livreur :
    - solde actuel
    - total gagné ce mois-ci (entrées)
    - dernières transactions
    """

    user = request.user

    # 1) Retrouver le livreur lié à l'utilisateur
    # Ici on part sur le mapping par email (le plus simple & propre)
    driver = DeliveryPartner.objects.filter(email=user.email).first()

    if not driver:
        messages.error(
            request,
            "Aucun profil livreur n'est lié à ce compte (email). "
            "Merci de vérifier l'adresse e-mail du livreur."
        )
        return redirect("orders:driver_app")

    # 2) Récupérer / créer le wallet du livreur
    wallet = get_or_create_wallet_for_delivery_partner(driver)

    # 3) Transactions récentes
    tx_qs = wallet.transactions.all().order_by("-created_at")[:50]

    # 4) Total gagné ce mois-ci (entrées)
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    month_in_qs = wallet.transactions.filter(
        created_at__gte=month_start,
        direction="in",
    )
    month_earnings = (
        month_in_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
    )

    context = {
        "driver": driver,
        "wallet": wallet,
        "transactions": tx_qs,
        "month_earnings": month_earnings,
        "month_start": month_start,
    }

    return render(request, "orders/driver_wallet.html", context)


def _get_connected_driver(request, order=None):
    """
    Essaie de retrouver le livreur connecté :
    - request.connected_driver (si tu l'utilises)
    - user.deliverypartner (si tu as un OneToOne)
    - fallback : order.delivery_partner (si order est fourni)
    """
    if hasattr(request, "connected_driver") and request.connected_driver:
        return request.connected_driver

    user = getattr(request, "user", None)
    if user is not None and hasattr(user, "deliverypartner"):
        return user.deliverypartner

    if order is not None:
        return getattr(order, "delivery_partner", None)

    return None

    connected_driver = _get_connected_driver(request)

    if not connected_driver and not selected_driver_id:
        return JsonResponse({
            "filtered_orders_count": 0,
            "pending": 0,
            "in_progress": 0,
            "done": 0,
            "canceled": 0,
            "total_orders": 0,
            "today_orders": 0,
            "total_distance_km": 0,
            "total_driver_income": 0,
            "source_distance": "—",
            "source_income": "—",
        })


def ensure_default_driver_legs(order, driver):
    """
    Crée les DeliveryLeg pour CE livreur / CETTE commande si nécessaire.

    - Utilise distance_km_pickup / distance_km_delivery si dispo
    - Sinon, découpe distance_km_total en 2
    - Répartit amount_driver_partner au prorata des distances
    """
    qs = DeliveryLeg.objects.filter(order=order, driver=driver).order_by("id")
    if qs.exists():
        return qs

    # --- Distances ---
    total_distance = (
        getattr(order, "distance_km_total", None)
        or getattr(order, "distance_km", None)
        or 0
    )

    pickup_distance = getattr(order, "distance_km_pickup", None)
    delivery_distance = getattr(order, "distance_km_delivery", None)

    # Cas 1 : on a les deux distances
    if pickup_distance is not None and delivery_distance is not None:
        pass  # on garde tel quel

    # Cas 2 : on n'a qu'une des deux → l'autre = total - connue (si cohérent)
    elif pickup_distance is not None and delivery_distance is None:
        if total_distance and pickup_distance <= total_distance:
            delivery_distance = max(total_distance - pickup_distance, 0)
        else:
            delivery_distance = pickup_distance  # fallback
    elif delivery_distance is not None and pickup_distance is None:
        if total_distance and delivery_distance <= total_distance:
            pickup_distance = max(total_distance - delivery_distance, 0)
        else:
            pickup_distance = delivery_distance  # fallback

    # Cas 3 : aucune distance spécifique → on coupe le total en 2
    else:
        if total_distance:
            pickup_distance = total_distance / 2
            delivery_distance = total_distance / 2
        else:
            pickup_distance = 0
            delivery_distance = 0

    # --- Montant livreur à répartir ---
    total_amount = getattr(order, "amount_driver_partner", None) or 0

    if pickup_distance + delivery_distance > 0 and total_amount:
        ratio_pickup = pickup_distance / (pickup_distance + delivery_distance)
        pickup_amount = round(total_amount * ratio_pickup)
        delivery_amount = total_amount - pickup_amount
    else:
        # Fallback : moitié–moitié
        pickup_amount = total_amount / 2 if total_amount else 0
        delivery_amount = total_amount / 2 if total_amount else 0

    # --- Création des 2 tronçons pour ce livreur ---
    legs = []

    legs.append(
        DeliveryLeg.objects.create(
            order=order,
            driver=driver,
            leg_type="pickup",
            distance_km=pickup_distance or 0,
            driver_amount=pickup_amount or 0,
            status="assigned",
        )
    )

    legs.append(
        DeliveryLeg.objects.create(
            order=order,
            driver=driver,
            leg_type="delivery",
            distance_km=delivery_distance or 0,
            driver_amount=delivery_amount or 0,
            status="assigned",
        )
    )

    return DeliveryLeg.objects.filter(order=order, driver=driver).order_by("id")


@login_required
def driver_order_detail(request, order_id):
    """
    Vue détail course côté LIVREUR.

    - Montre uniquement les infos utiles au chauffeur
    - Calcule la distance & le montant qui lui reviennent
      à partir des DeliveryLeg
    - Crée des DeliveryLeg par défaut s'il n'y en a pas encore
    """

    order = get_object_or_404(
        Order.objects.select_related("customer", "delivery_partner", "laundry_partner"),
        pk=order_id,
    )

    # Pour l’instant, on se base sur le livreur assigné à la commande
    driver = order.delivery_partner

    # Valeurs par défaut
    driver_legs_qs = DeliveryLeg.objects.none()
    driver_leg_distance = 0
    driver_leg_amount = 0
    driver_mission_type_label = "Mission unique (A/R ou globale)"
    driver_wallet = None
    driver_wallet_url = ""

    if driver is not None:
        # 1) On s’assure qu’il y a au moins des legs pour (order, driver)
        driver_legs_qs = ensure_default_driver_legs(order, driver)

        # 2) Agrégats distance / montant pour CE livreur
        legs_agg = driver_legs_qs.aggregate(
            total_distance_km=Sum("distance_km"),
            total_amount=Sum("driver_amount"),
        )
        driver_leg_distance = legs_agg["total_distance_km"] or 0
        driver_leg_amount = legs_agg["total_amount"] or 0

        # 3) Fallback pour très anciennes commandes sans legs
        if driver_leg_distance == 0 and getattr(order, "distance_km_total", None):
            driver_leg_distance = order.distance_km_total
        if driver_leg_amount == 0 and getattr(order, "amount_driver_partner", None):
            driver_leg_amount = order.amount_driver_partner

        # 4) Libellé type de mission à partir des leg_type
        leg_types = list(
            driver_legs_qs.values_list("leg_type", flat=True).distinct()
        )
        if not leg_types:
            driver_mission_type_label = "Mission unique (A/R ou globale)"
        elif len(leg_types) == 1:
            lt = leg_types[0]
            if lt == "pickup":
                driver_mission_type_label = "Collecte (client → blanchisserie)"
            elif lt == "delivery":
                driver_mission_type_label = "Livraison (blanchisserie → client)"
            elif lt == "round_trip":
                driver_mission_type_label = "Collecte + livraison (A/R complet)"
            else:
                driver_mission_type_label = "Mission particulière"
        else:
            driver_mission_type_label = "Collecte + livraison (tronçons multiples)"

        # 5) Wallet livreur
        try:
            driver_wallet = get_or_create_wallet_for_delivery_partner(driver)
            driver_wallet_url = (
                reverse("wallets:driver_wallet_dashboard") + f"?driver_id={driver.id}"
            )
        except Exception:
            driver_wallet = None
            driver_wallet_url = ""

    # --- Montants FAGNI cohérents (client + revenus) ---
    try:
        data = order.compute_totals(save=False) or {}
    except Exception:
        data = {}

    amounts = data

    total_client_ttc = (
        amounts.get("total_client_ttc")
        or getattr(order, "total_client_ttc", None)
        or getattr(order, "total", None)
        or getattr(order, "prestation_total", None)
        or Decimal("0")
    )

    delivery_fee_client = (
        amounts.get("delivery_fee_client")
        or getattr(order, "delivery_fee", None)
        or Decimal("0")
    )

    # Revenu livreur : priorité aux legs, puis mêmes fallbacks que driver_app
    driver_income = driver_leg_amount or (
        amounts.get("amount_driver_partner")
        or getattr(order, "amount_driver_partner_resolved", None)
        or getattr(order, "amount_driver_partner", None)
        or getattr(order, "driver_logistic_cost", None)
        or Decimal("0")
    )

    from orders.utils.geo import (
        resolve_pickup_coords,
        resolve_delivery_coords,
        resolve_provider_coords,
    )

    pickup_coords = resolve_pickup_coords(order)
    delivery_coords = resolve_delivery_coords(order)
    provider_coords = resolve_provider_coords(order)

    pickup_lat, pickup_lng = pickup_coords if pickup_coords else (None, None)
    delivery_lat, delivery_lng = delivery_coords if delivery_coords else (None, None)
    provider_lat, provider_lng = provider_coords if provider_coords else (None, None)

    context = {
        "order": order,
        "driver": driver,
        "driver_legs_qs": driver_legs_qs,
        "driver_leg_distance": driver_leg_distance,
        "driver_leg_amount": driver_leg_amount,
        "driver_mission_type_label": driver_mission_type_label,
        "driver_wallet": driver_wallet,
        "driver_wallet_url": driver_wallet_url,
        "amounts": amounts,
        "total_client_ttc": total_client_ttc,
        "delivery_fee_client": delivery_fee_client,
        "driver_income": driver_income,
        "pickup_coords": pickup_coords,
        "delivery_coords": delivery_coords,
        "provider_coords": provider_coords,
        "pickup_lat": pickup_lat,
        "pickup_lng": pickup_lng,
        "delivery_lat": delivery_lat,
        "delivery_lng": delivery_lng,
        "provider_lat": provider_lat,
        "provider_lng": provider_lng,
    }
    return render(request, "orders/driver_order_detail.html", context)


# ===============================================
#  APP LIVREUR – DATA JSON pour refresh KPIs
# ===============================================
@login_required
def driver_app_data(request):
    """
    Version JSON des KPIs App Livreurs.
    Utilisée par le JS en AJAX dans driver_app.html.

    Harmonisation FAGNI :
    - Distance = somme legs.distance_km si legs existent sinon fallback order.distance_km_total/distance_km
    - Revenu  = somme legs.driver_amount si legs existent sinon fallback amount_driver_partner / driver_logistic_cost
    """
    connected_driver = _get_connected_driver(request)

    qs = Order.objects.all()

    selected_driver_id = (request.GET.get("driver_id") or "").strip()
    status_filter = (request.GET.get("status") or "active").strip()

    # Filtre par livreur
    if connected_driver:
        qs = qs.filter(delivery_partner=connected_driver)
    elif selected_driver_id:
        qs = qs.filter(delivery_partner_id=selected_driver_id)

    # Filtre par statut
    if status_filter == "active":
        qs = qs.filter(status__in=["pending", "in_progress"])
    elif status_filter == "done":
        qs = qs.filter(status="done")
    elif status_filter == "canceled":
        qs = qs.filter(status="canceled")
    # "all" => pas de filtre supplémentaire

    filtered_orders_count = qs.count()
    pending = qs.filter(status="pending").count()
    in_progress = qs.filter(status="in_progress").count()
    done = qs.filter(status="done").count()
    canceled = qs.filter(status="canceled").count()

    # Stats globales pour le livreur (toutes ses courses)
    today = timezone.localdate()
    today_start = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.min.time())
    )
    today_end = timezone.make_aware(
        timezone.datetime.combine(today, timezone.datetime.max.time())
    )

    base_for_driver_stats = Order.objects.all()
    if connected_driver:
        base_for_driver_stats = base_for_driver_stats.filter(delivery_partner=connected_driver)
    elif selected_driver_id:
        base_for_driver_stats = base_for_driver_stats.filter(delivery_partner_id=selected_driver_id)

    total_orders = base_for_driver_stats.count()
    today_orders = base_for_driver_stats.filter(
        created_at__gte=today_start,
        created_at__lte=today_end,
    ).count()

    # --- Legs aggregation (distance + income) ---
    order_ids = list(qs.values_list("id", flat=True))
    legs_income_map = {}
    legs_distance_map = {}

    if order_ids:
        legs_rows = (
            DeliveryLeg.objects
            .filter(order_id__in=order_ids)
            .values("order_id")
            .annotate(
                total_income=Sum("driver_amount"),
                total_dist=Sum("distance_km"),
            )
        )
        legs_income_map = {r["order_id"]: (r["total_income"] or 0) for r in legs_rows}
        legs_distance_map = {r["order_id"]: (r["total_dist"] or 0) for r in legs_rows}

    total_distance = Decimal("0")
    total_income = Decimal("0")

    used_legs_for_distance = False
    used_legs_for_income = False

    # On itère sur les commandes filtrées
    for o in qs.only("id", "distance_km_total", "distance_km", "amount_driver_partner", "driver_logistic_cost"):
        legs_income = Decimal(str(legs_income_map.get(o.id, 0) or 0))
        legs_dist = Decimal(str(legs_distance_map.get(o.id, 0) or 0))

        # Distance à afficher (utile si un jour tu refresh les cartes via JSON)
        driver_distance_display = (
            legs_dist
            or Decimal(str(getattr(o, "distance_km_total", None) or getattr(o, "distance_km", None) or 0))
        )
        o.driver_distance_display = float(driver_distance_display or 0)

        # distance
        if legs_dist > 0:
            total_distance += legs_dist
            used_legs_for_distance = True
        else:
            fallback_dist = getattr(o, "distance_km_total", None) or getattr(o, "distance_km", None) or 0
            total_distance += Decimal(str(fallback_dist or 0))


        # income
        if legs_income > 0:
            total_income += legs_income
            used_legs_for_income = True
        else:
            fallback_income = getattr(o, "amount_driver_partner", None) or getattr(o, "driver_logistic_cost", None) or 0
            total_income += Decimal(str(fallback_income or 0))

    data = {
        "filtered_orders_count": filtered_orders_count,
        "pending": pending,
        "in_progress": in_progress,
        "done": done,
        "canceled": canceled,
        "total_orders": total_orders,
        "today_orders": today_orders,
        "total_distance_km": float(total_distance.quantize(Decimal("0.01"))),
        "total_driver_income": float(total_income.quantize(Decimal("0.01"))),
        "source_distance": "legs" if used_legs_for_distance else "order_fallback",
        "source_income": "legs" if used_legs_for_income else "order_fallback",
    }
    return JsonResponse(data)


@login_required
def driver_app(request):
    """
    Vue principale "Mes courses" pour le livreur.

    Harmonisation montants FAGNI :
    - Montant client = compute_totals().total_client_ttc (fallback total / prestation_total)
    - Frais livraison client = compute_totals().delivery_fee_client (fallback delivery_fee)
    - Revenu livreur = somme DeliveryLeg.driver_amount (si legs) sinon amount_driver_partner_resolved
      sinon driver_logistic_cost.
    """
    connected_driver = get_connected_driver(request)  # ✅ helper existant

    if not connected_driver:
        messages.error(
            request,
            "Aucun profil livreur détecté. Merci de vérifier l'adresse e-mail ou de passer ?driver_id=XX."
        )
        context = {
            "connected_driver": None,
            "pending_orders": [],
            "in_progress_orders": [],
            "done_orders": [],
            "pending_count": 0,
            "in_progress_count": 0,
            "done_count": 0,
        }
        return render(request, "orders/driver_app.html", context)

    # Query principale
    base_qs = (
        Order.objects
        .filter(delivery_partner=connected_driver)
        .exclude(status="canceled")
        .select_related("customer", "laundry_partner", "delivery_partner")
        .prefetch_related("items")
        .order_by("-created_at")
    )

    pending_orders = list(base_qs.filter(status="pending"))
    in_progress_orders = list(base_qs.filter(status="in_progress"))
    done_orders = list(base_qs.filter(status="done")[:50])

    # --- Legs (1 seule requête d'agrégation pour toutes les commandes affichées) ---
    all_orders = pending_orders + in_progress_orders + done_orders
    order_ids = [o.id for o in all_orders]

    legs_income_map = {}
    legs_distance_map = {}

    if order_ids:
        legs_rows = (
            DeliveryLeg.objects
            .filter(driver=connected_driver, order_id__in=order_ids)
            .values("order_id")
            .annotate(
                income=Sum("driver_amount"),
                dist=Sum("distance_km"),
            )
        )
        legs_income_map = {r["order_id"]: (r["income"] or 0) for r in legs_rows}
        legs_distance_map = {r["order_id"]: (r["dist"] or 0) for r in legs_rows}

    def _enrich(o):
        # compute_totals ne sauvegarde pas ici
        try:
            amounts = o.compute_totals(save=False) or {}
        except Exception:
            amounts = {}

        # --- Montant total client TTC ---
        total_client_display = (
            amounts.get("total_client_ttc")
            or getattr(o, "total_client_ttc", None)
            or getattr(o, "total", None)
            or getattr(o, "prestation_total", None)
            or 0
        )

        # --- Frais livraison client ---
        delivery_fee_client_display = (
            amounts.get("delivery_fee_client")
            or getattr(o, "delivery_fee", None)
            or 0
        )

        # --- Legs (revenu + distance) ---
        legs_income = Decimal(str(legs_income_map.get(o.id, 0) or 0))
        legs_dist = Decimal(str(legs_distance_map.get(o.id, 0) or 0))

        # --- Distance à afficher (priorité legs) ---
        driver_distance_display = (
            legs_dist
            or Decimal(str(getattr(o, "distance_km_total", None) or getattr(o, "distance_km", None) or 0))
        )

        # --- Revenu livreur (priorité legs) ---
        driver_income_display = (
            legs_income
            or amounts.get("amount_driver_partner")
            or getattr(o, "amount_driver_partner_resolved", None)
            or getattr(o, "amount_driver_partner", None)
            or getattr(o, "driver_logistic_cost", None)
            or 0
        )

        # --- Paiement (PAYÉ / PARTIEL / À ENCAISSER) ---
        try:
            amount_paid_display = Decimal(str(getattr(o, "amount_paid", None) or 0))
        except Exception:
            amount_paid_display = Decimal("0")

        try:
            total_dec = Decimal(str(total_client_display or 0))
        except Exception:
            total_dec = Decimal("0")

        due_amount_display = total_dec - amount_paid_display
        if due_amount_display < 0:
            due_amount_display = Decimal("0")

        pay_status = (getattr(o, "payment_status", "") or "").lower()
        if pay_status == "paid" or due_amount_display <= 0:
            pay_status_label = "PAYÉ"
            due_amount_display = Decimal("0")
        elif amount_paid_display > 0:
            pay_status_label = "PARTIEL"
        else:
            pay_status_label = "À ENCAISSER"

        # --- Injection des champs DISPLAY pour le template ---
        o.total_client_display = total_client_display
        o.delivery_fee_client_display = delivery_fee_client_display
        o.driver_income_display = driver_income_display
        o.driver_distance_display = float(driver_distance_display or 0)

        return o

    pending_orders = [_enrich(o) for o in pending_orders]
    in_progress_orders = [_enrich(o) for o in in_progress_orders]
    done_orders = [_enrich(o) for o in done_orders]

    context = {
        "connected_driver": connected_driver,
        "pending_orders": pending_orders,
        "in_progress_orders": in_progress_orders,
        "done_orders": done_orders,
        "pending_count": len(pending_orders),
        "in_progress_count": len(in_progress_orders),
        "done_count": base_qs.filter(status="done").count(),
    }
    return render(request, "orders/driver_app.html", context)


# ===============================================
#  APP LIVREUR – EXPORT CSV / XLSX
# ===============================================
@login_required
def driver_app_export_csv(request):
    """
    Export CSV des courses visibles dans l'App livreur.

    - Si user livreur : uniquement SES courses
    - Si user staff : possibilité de filtrer par driver_id
    - Montants harmonisés FAGNI :
        * total_client_display = total_client_ttc (si dispo) OU fallback
        * driver_income_display = amount_driver_partner (fallback driver_logistic_cost)
    """
    user = request.user
    connected_driver = _get_connected_driver(request)

    qs = Order.objects.select_related(
        "customer",
        "delivery_partner",
        "laundry_partner",
    )

    driver_id = (request.GET.get("driver_id") or "").strip()
    status_filter = (request.GET.get("status") or "active").strip()

    # --- Filtre livreur ---
    if connected_driver and not user.is_staff:
        # Mode LIVREUR : uniquement ses propres courses
        qs = qs.filter(delivery_partner=connected_driver)
    elif driver_id:
        qs = qs.filter(delivery_partner_id=driver_id)

    # --- Filtre statut (même logique que driver_app_data) ---
    if status_filter == "active":
        qs = qs.filter(status__in=["pending", "in_progress"])
    elif status_filter == "done":
        qs = qs.filter(status="done")
    elif status_filter == "canceled":
        qs = qs.filter(status="canceled")
    # "all" => pas de filtre supplémentaire

    qs = qs.order_by("-created_at")

    # --- Expressions de montants harmonisés FAGNI ---
    DEC = DecimalField(max_digits=12, decimal_places=2)

    items_total_expr = Coalesce(
        Sum(
            Cast(F("items__quantity"), DEC)
            * Cast(F("items__unit_price"), DEC)
        ),
        Value(0, output_field=DEC),
    )

    total_client_expr = Coalesce(
        F("total_client_ttc"),
        Coalesce(F("total"), items_total_expr)
        + Coalesce(F("delivery_fee"), Value(0, output_field=DEC))
        + Coalesce(F("service_fee"), Value(0, output_field=DEC))
        + Coalesce(F("vat_fagni"), Value(0, output_field=DEC)),
        output_field=DEC,
    )

    income_expr = Coalesce(
        F("amount_driver_partner"),
        Coalesce(F("driver_logistic_cost"), Value(0, output_field=DEC)),
        output_field=DEC,
    )

    qs = qs.annotate(
        items_total=items_total_expr,
        total_client_display=total_client_expr,
        driver_income_display=income_expr,
    )

    # --- Réponse CSV ---
    response = HttpResponse(content_type="text/csv")

    filename_parts = ["driver_app"]
    if connected_driver and not user.is_staff:
        filename_parts.append(f"driver_{connected_driver.id}")
    elif driver_id:
        filename_parts.append(f"driver_{driver_id}")

    filename = "_".join(filename_parts) + ".csv"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response, delimiter=";")

    # En-têtes
    writer.writerow([
        "Code commande",
        "Date création",
        "Client",
        "Téléphone",
        "Adresse",
        "Statut",
        "Distance_km",
        "Montant client TTC",
        "Revenu livreur (FCFA)",
    ])

    # Lignes
    for order in qs:
        customer = getattr(order, "customer", None)

        writer.writerow([
            order.code or order.id,
            order.created_at.strftime("%Y-%m-%d %H:%M") if order.created_at else "",
            getattr(customer, "name", "") if customer else "",
            getattr(customer, "phone", "") if customer else "",
            getattr(customer, "address", "") if customer else "",
            order.get_status_display(),
            float(order.distance_km) if order.distance_km is not None else 0,
            # total client TTC harmonisé
            float(order.total_client_display) if getattr(order, "total_client_display", None) is not None else 0,
            # revenu livreur harmonisé
            float(order.driver_income_display) if getattr(order, "driver_income_display", None) is not None else 0,
        ])

    return response


#@login_required
#def driver_app_export_xlsx(request):
#    """
#    Alias simple vers l'export CSV pour compatibilité.
 #   (On garde le même contenu, tu pourras l'ouvrir dans Excel et
#    mettre en forme avec ton template premium.)
 #   """
   # return driver_app_export_csv(request)


@login_required
def driver_app_export_xlsx(request):
    """
    Export XLSX premium des courses livreur, avec :
    - filtres par livreur + statut (même logique que driver_app / driver_app_export_csv)
    - montants alignés sur le modèle FAGNI :
        * total_client_ttc (fallback : prestation_total + service_fee + delivery_fee + vat_fagni)
        * revenu livreur = amount_driver_partner (fallback driver_logistic_cost)
    - mise en forme Excel : en-têtes stylées, auto-filter, freeze pane, totaux avec formules.
    """
    user = request.user
    connected_driver = _get_connected_driver(request)
    driver_id = None

   # --- Identification du livreur (livreur connecté ou driver_id en GET pour un staff) ---
    if connected_driver:
        driver_id = connected_driver.id
    elif user.is_staff:
        driver_id_param = request.GET.get("driver_id") or ""
        if driver_id_param:
            try:
                driver_id = int(driver_id_param)
            except ValueError:
                driver_id = None

    status_filter = (request.GET.get("status") or "active").strip()

    # On force un DecimalField commun
    DEC = DecimalField(max_digits=12, decimal_places=2)

    # Base queryset (mêmes relations que l'app livreur)
    qs = (
        Order.objects
        .select_related("customer", "delivery_partner", "laundry_partner")
        .prefetch_related("items")
        .order_by("-created_at")
    )

    if driver_id:
        qs = qs.filter(delivery_partner_id=driver_id)

    # Filtre statut (même logique que driver_app / driver_app_data / driver_orders_csv)
    if status_filter == "active":
        qs = qs.filter(status__in=["pending", "in_progress"])
    elif status_filter == "done":
        qs = qs.filter(status="done")
    elif status_filter == "canceled":
        qs = qs.filter(status="canceled")
    # status_filter == "all" => pas de filtre supplémentaire

    # ---------- ANNOTATIONS FINANCIÈRES ALIGNÉES FAGNI ----------
    # Sous-total prestations = somme (quantity * unit_price)
    items_total_expr = Coalesce(
        Sum(
            Cast(F("items__quantity"), DEC)
            * Cast(F("items__unit_price"), DEC)
        ),
        Value(0, output_field=DEC),
    )

    # Base prestations = prestation_total (si rempli) sinon somme des lignes
    prestation_expr = Coalesce(F("prestation_total"), items_total_expr, output_field=DEC)

    service_expr = Coalesce(F("service_fee"), Value(0, output_field=DEC))
    delivery_expr = Coalesce(F("delivery_fee"), Value(0, output_field=DEC))
    vat_expr = Coalesce(F("vat_fagni"), Value(0, output_field=DEC))

    base_ht_expr = prestation_expr + service_expr + delivery_expr
    total_client_fallback = base_ht_expr + vat_expr

    driver_income_expr = Coalesce(
        F("amount_driver_partner"),
        Coalesce(F("driver_logistic_cost"), Value(0, output_field=DEC)),
        output_field=DEC,
    )

    qs = qs.annotate(
        items_total=items_total_expr,
        total_client_display=Coalesce(
            F("total_client_ttc"),
            total_client_fallback,
            output_field=DEC,
        ),
        driver_income_display=driver_income_expr,
    )

    # ---------- CRÉATION DU FICHIER EXCEL ----------
    wb = Workbook()
    ws = wb.active
    ws.title = "Courses livreur"

    # Styles de base
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2563EB")  # bleu
    header_alignment = Alignment(horizontal="center", vertical="center")

    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    money_format = "#,##0"  # affichage 1 000 / 10 000 etc. (selon Excel)

    # En-têtes
    headers = [
        "Code commande",
        "Date création",
        "Client",
        "Téléphone",
        "Adresse",
        "Statut",
        "Distance (km)",
        "Total client TTC (FCFA)",
        "Revenu livreur (FCFA)",
    ]

    ws.append(headers)

    for col_idx, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    # Lignes
    row_index = 1
    for order in qs:
        row_index += 1
        customer = getattr(order, "customer", None)

        code = order.code or str(order.id)
        created = order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else ""
        client_name = getattr(customer, "name", "") if customer else ""
        phone = getattr(customer, "phone", "") if customer else ""
        address = getattr(customer, "address", "") if customer else ""
        status_display = order.get_status_display()
        distance_km = float(order.distance_km) if order.distance_km is not None else 0.0
        total_client = float(order.total_client_display or 0)
        driver_income = float(order.driver_income_display or 0)

        row_values = [
            code,
            created,
            client_name,
            phone,
            address,
            status_display,
            distance_km,
            total_client,
            driver_income,
        ]
        ws.append(row_values)

        # Bordures + formats
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_index, column=col_idx)
            cell.border = thin_border
            if col_idx in (7, 8, 9):  # distance + montants
                cell.number_format = money_format

    last_data_row = row_index

    # ---------- LIGNE TOTAUX ----------
    total_row = last_data_row + 1
    ws.cell(row=total_row, column=1, value="Totaux :").font = Font(bold=True)

    # Total distance
    ws.cell(
        row=total_row,
        column=7,
        value=f"=SUM({get_column_letter(7)}2:{get_column_letter(7)}{last_data_row})",
    ).number_format = money_format

    # Total client TTC
    ws.cell(
        row=total_row,
        column=8,
        value=f"=SUM({get_column_letter(8)}2:{get_column_letter(8)}{last_data_row})",
    ).number_format = money_format

    # Total revenu livreur
    ws.cell(
        row=total_row,
        column=9,
        value=f"=SUM({get_column_letter(9)}2:{get_column_letter(9)}{last_data_row})",
    ).number_format = money_format

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=total_row, column=col_idx)
        cell.border = thin_border
        if col_idx >= 7:
            cell.font = Font(bold=True)

    # ---------- AUTO-FILTER + FREEZE PANES + LARGEUR COLONNES ----------
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{last_data_row}"
    ws.freeze_panes = "A2"

    # Largeurs colonnes
    widths = {
        1: 16,  # Code
        2: 18,  # Date
        3: 24,  # Client
        4: 16,  # Téléphone
        5: 40,  # Adresse
        6: 16,  # Statut
        7: 14,  # Distance
        8: 22,  # Total client
        9: 22,  # Revenu livreur
    }
    for col_idx, width in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # ---------- RÉPONSE HTTP ----------
    # Nom de fichier
    base_name = "fagni_courses_livreur"
    if driver_id:
        base_name += f"_driver_{driver_id}"
    if status_filter:
        base_name += f"_{status_filter}"
    filename = base_name + ".xlsx"

    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    wb.save(response)
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
    Espace 'Moi' du livreur :
    - stats globales (total courses, du mois, distance, revenus)
    - stats d'activité du jour (en attente / en cours / terminées / annulées)
    """

    user = request.user

    # On matche le livreur par email (ajuste si tu utilises un autre lien)
    try:
        driver = DeliveryPartner.objects.get(email=user.email)
    except DeliveryPartner.DoesNotExist:
        # fallback : tu peux rediriger vers le hub ou afficher un message
        return redirect("orders:driver_hub")

    today = timezone.localdate()

    # Toutes les courses du livreur
    qs = Order.objects.filter(delivery_partner=driver)

    # Période du mois en cours
    month_qs = qs.filter(
        created_at__year=today.year,
        created_at__month=today.month,
    )

    # Activité du jour
    today_qs = qs.filter(created_at__date=today)

    # --- Agrégats simples (sans distance) ---
    raw_stats = qs.aggregate(
        total_orders=Count("id", distinct=True),
        # adapte ce filtre si tu as déjà un start_month dans ta fonction
        month_orders=Count(
            "id",
            filter=Q(created_at__gte=start_month) if "start_month" in locals() else Q(),
            distinct=True,
        ),
        total_income=Coalesce(
            Sum("driver_logistic_cost"),
            Decimal("0.0"),
        ),
    )

    # --- Distance totale en Python (on évite l'expression mixte ORM) ---
    legs_qs = DeliveryLeg.objects.filter(
        order__in=qs,
        distance_km__isnull=False,
    ).values_list("distance_km", flat=True)

    total_distance_km = Decimal("0.0")
    for d in legs_qs:
        # d est un float ou Decimal → on le convertit proprement en Decimal
        if d is not None:
            total_distance_km += Decimal(str(d))

    # On peut arrondir à 1 décimale si tu veux un rendu propre
    total_distance_km = total_distance_km.quantize(Decimal("0.1"))

    stats = {
        "total_orders": raw_stats["total_orders"] or 0,
        "month_orders": raw_stats["month_orders"] or 0,
        "total_distance_km": total_distance_km,
        "total_income": raw_stats["total_income"] or Decimal("0.0"),
    }

    context = {
        "driver": driver,
        "stats": stats,
        "today": today,
        "orders": qs.order_by("-created_at")[:10],  # dernières courses, par ex
    }
    return render(request, "orders/driver_me.html", context)


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
#  APP LIVREUR – DATA JSON pour refresh KPIs
# ===============================================
@login_required
def driver_me_data(request):
    """
    Endpoint JSON pour l’auto-refresh des KPI de driver_me_app.

    Montants harmonisés FAGNI :
    - Revenu livreur = amount_driver_partner (fallback driver_logistic_cost)
    """
    user_email = (request.user.email or "").strip()
    if not user_email:
        return JsonResponse({"error": "no_email"}, status=403)

    try:
        driver = DeliveryPartner.objects.get(email__iexact=user_email)
    except DeliveryPartner.DoesNotExist:
        return JsonResponse({"error": "no_driver_profile"}, status=403)

    today = timezone.localdate()

    # Toutes les courses du jour pour ce livreur
    qs_today = Order.objects.filter(
        delivery_partner=driver,
        created_at__date=today,
    )

    # Comptages par statut
    total_orders_today = qs_today.count()
    pending_today = qs_today.filter(status="pending").count()
    in_progress_today = qs_today.filter(status="in_progress").count()
    done_today = qs_today.filter(status="done").count()
    canceled_today = qs_today.filter(status="canceled").count()

    # Agrégats distance + revenu livreur (harmonisé)
    DEC = DecimalField(max_digits=12, decimal_places=2)

    income_expr = Coalesce(
        F("amount_driver_partner"),
        Coalesce(F("driver_logistic_cost"), Value(0, output_field=DEC)),
        output_field=DEC,
    )

    aggregates = qs_today.aggregate(
        total_distance_km=Sum("distance_km"),
        total_driver_income=Sum(income_expr),
    )

    total_distance_km_today = float(aggregates["total_distance_km"] or 0)
    total_driver_income_today = float(aggregates["total_driver_income"] or 0)

    data = {
        "date": str(today),
        "total_orders_today": total_orders_today,
        "pending_today": pending_today,
        "in_progress_today": in_progress_today,
        "done_today": done_today,
        "canceled_today": canceled_today,
        "total_distance_km_today": round(total_distance_km_today, 2),
        "total_driver_income_today": round(total_driver_income_today, 2),
    }

    return JsonResponse(data)


# ===============================================
#  DÉTAIL COURSE – APP LIVREUR
# ===============================================
@login_required
def driver_kpi(request):
    """
    Vue KPI livreur :
    - Si user staff : choix du livreur + filtres
    - Si user livreur : KPI uniquement sur ses propres courses

    Montants harmonisés avec FAGNI :
    - Revenu livreur = amount_driver_partner (fallback driver_logistic_cost)
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

    # --- Agrégats globaux harmonisés FAGNI ---
    DEC = DecimalField(max_digits=12, decimal_places=2)

    driver_income_expr = Coalesce(
        F("amount_driver_partner"),
        Coalesce(F("driver_logistic_cost"), Value(0, output_field=DEC)),
        output_field=DEC,
    )

    aggregates = orders_qs.aggregate(
        total_orders=Count("id"),
        total_distance_km=Sum("distance_km"),
        total_driver_income=Sum(driver_income_expr),
    )

    total_orders = aggregates.get("total_orders") or 0
    total_distance_km = aggregates.get("total_distance_km") or 0
    total_driver_income = aggregates.get("total_driver_income") or 0

    avg_distance_km = total_distance_km / total_orders if total_orders else 0
    avg_driver_income = total_driver_income / total_orders if total_orders else 0

    # --- Répartition par statut ---
    status_counts = {"pending": 0, "in_progress": 0, "done": 0, "canceled": 0}
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

    ⚠️ Revenu livreur harmonisé avec le modèle FAGNI :
    - on prend en priorité amount_driver_partner
    - sinon driver_logistic_cost
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

        # Revenu livreur harmonisé FAGNI :
        # 1) amount_driver_partner (montant dû au livreur)
        # 2) fallback : driver_logistic_cost
        driver_income = order.amount_driver_partner
        if driver_income is None or driver_income == 0:
            driver_income = order.driver_logistic_cost or 0

        writer.writerow([
            order.code or order.id,
            order.created_at.strftime("%Y-%m-%d %H:%M") if order.created_at else "",
            getattr(customer, "name", "") if customer else "",
            getattr(customer, "phone", "") if customer else "",
            getattr(customer, "address", "") if customer else "",
            order.get_status_display(),
            float(order.distance_km) if order.distance_km is not None else 0,
            float(driver_income),
        ])

    return response


@login_required
def driver_history_me(request):
    """
    Historique des courses du livreur connecté :
    - filtre par période (7j, 30j, mois en cours)
    - filtre par statut
    - bilan : nb courses, distance, revenus, cash à remettre

    Montants harmonisés avec FAGNI :
    - Montant client = total_client_ttc (compute_totals) fallback total_client_ttc / total
    - Revenu livreur = priorité aux legs (DeliveryLeg.driver_amount), sinon amount_driver_partner,
      sinon driver_logistic_cost, sinon estimation distance * 75
    - Cash à remettre (approche cash simplifiée) = montant client - revenu livreur (>= 0)
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
    period = (request.GET.get("period") or "7d").strip()
    status_filter = (request.GET.get("status") or "all").strip()

    # Période
    if period == "30d":
        start_date = today - timedelta(days=30)
    elif period == "month":
        start_date = today.replace(day=1)
    else:  # "7d"
        period = "7d"
        start_date = today - timedelta(days=7)

    qs = (
        Order.objects
        .filter(
            delivery_partner=delivery_partner,
            created_at__date__gte=start_date,
            created_at__date__lte=today,
        )
        .select_related("customer", "laundry_partner")
        .prefetch_related("items", "items__photos")
    )

    # Statut
    if status_filter == "active":
        qs = qs.filter(status__in=["pending", "in_progress"])
    elif status_filter in ["pending", "in_progress", "done", "canceled"]:
        qs = qs.filter(status=status_filter)
    else:
        status_filter = "all"

    orders = list(qs.order_by("-created_at"))

    # Agrégats / calculs par ligne
    total_orders = len(orders)
    done_count = sum(1 for o in orders if o.status == "done")
    total_distance_km = qs.aggregate(total=Sum("distance_km"))["total"] or 0

    total_driver_earnings = Decimal("0.00")
    client_total = Decimal("0.00")

    # Tarif de secours par km si aucun revenu renseigné
    fallback_price_per_km = Decimal("75")

    DEC = DecimalField(max_digits=12, decimal_places=2)

    for o in orders:
        # 1) Montants client (source de vérité = compute_totals)
        data = {}
        try:
            data = o.compute_totals(save=False) or {}
        except Exception:
            data = {}

        total_client = (
            data.get("total_client_ttc")
            or getattr(o, "total_client_ttc", None)
            or getattr(o, "total", None)
            or Decimal("0.00")
        )
        try:
            total_client = Decimal(str(total_client))
        except Exception:
            total_client = Decimal("0.00")

        o.total_client = total_client
        client_total += total_client

        # 2) Revenu livreur : priorité aux legs (si la mission est découpée)
        legs_agg = DeliveryLeg.objects.filter(
            order=o,
            driver=delivery_partner,
        ).aggregate(
            total_amount=Sum("driver_amount", output_field=DEC),
            total_distance=Sum("distance_km", output_field=DEC),
        )

        legs_amount = legs_agg.get("total_amount") or Decimal("0.00")
        if legs_amount:
            earning = legs_amount
        else:
            earning = (
                data.get("amount_driver_partner")
                or getattr(o, "amount_driver_partner", None)
                or getattr(o, "driver_logistic_cost", None)
                or Decimal("0.00")
            )

        try:
            earning = Decimal(str(earning))
        except Exception:
            earning = Decimal("0.00")

        # 3) Dernier filet : distance * 75
        if (earning is None or earning == 0) and o.distance_km:
            try:
                earning = Decimal(str(o.distance_km)) * fallback_price_per_km
            except Exception:
                earning = Decimal("0.00")

        o.driver_earnings_display = earning
        total_driver_earnings += earning

    # Cash à remettre = montant client - revenus livreur (simplifié)
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


@transaction.atomic
def ensure_delivery_legs_for_order(order):
    """
    S'assure que la commande a des DeliveryLeg cohérents pour le livreur.

    - Si des legs existent déjà -> on ne recrée rien.
    - Sinon, on crée au minimum :
        * 1 leg 'pickup'  (Client -> Blanchisserie)
        * 1 leg 'return'  (Blanchisserie -> Client)
    - La distance et le montant livreur sont répartis entre les jambes.
    """

    # Est-ce qu'il y a déjà des legs pour cette commande ?
    existing_legs = DeliveryLeg.objects.filter(order=order)
    if existing_legs.exists():
        return existing_legs

    # Pas de livreur -> on ne crée rien (logique de fallback globale restera utilisée)
    driver = getattr(order, "delivery_partner", None)
    if not driver:
        return existing_legs

    # ==== Distances ====
    # On récupère ce qu'on peut depuis la commande
    pickup_dist = getattr(order, "distance_km_pickup", None)
    delivery_dist = getattr(order, "distance_km_delivery", None)

    total_command_dist = (
        getattr(order, "distance_km_total", None)
        or getattr(order, "distance_km", None)
    )

    # Normalisation : float ou 0
    def _to_float_or_none(v):
        try:
            if v is None:
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    pickup_dist = _to_float_or_none(pickup_dist)
    delivery_dist = _to_float_or_none(delivery_dist)
    total_command_dist = _to_float_or_none(total_command_dist)

    # Si pickup + delivery sont absents ou incohérents, on se base sur la distance totale
    if (pickup_dist is None or pickup_dist <= 0) or (delivery_dist is None or delivery_dist <= 0):
        if total_command_dist and total_command_dist > 0:
            # Répartition simple 50/50
            pickup_dist = round(total_command_dist / 2, 3)
            delivery_dist = round(total_command_dist - pickup_dist, 3)
        else:
            # Aucun chiffre fiable -> on laisse à 0
            pickup_dist = 0.0
            delivery_dist = 0.0

    # ==== Montant livreur ====
    total_driver_amount = getattr(order, "amount_driver_partner", None)
    try:
        total_driver_amount = float(total_driver_amount or 0)
    except (TypeError, ValueError):
        total_driver_amount = 0.0

    pickup_amount = 0.0
    delivery_amount = 0.0

    if total_driver_amount > 0:
        dist_sum = (pickup_dist or 0) + (delivery_dist or 0)
        if dist_sum > 0:
            # Répartition proportionnelle à la distance
            pickup_amount = round(total_driver_amount * (pickup_dist / dist_sum))
            delivery_amount = round(total_driver_amount - pickup_amount)
        else:
            # Fallback 50/50
            pickup_amount = round(total_driver_amount / 2)
            delivery_amount = round(total_driver_amount - pickup_amount)

    now = timezone.now()

    legs_to_create = []

    # Leg PICKUP : Client -> Blanchisserie
    legs_to_create.append(
        DeliveryLeg(
            order=order,
            driver=driver,
            leg_type="pickup",
            status="assigned",
            distance_km=pickup_dist,
            driver_amount=pickup_amount,
            client_fee_share=0,
            fagni_margin=0,
            started_at=None,
            finished_at=None,
        )
    )

    # Leg RETURN : Blanchisserie -> Client
    legs_to_create.append(
        DeliveryLeg(
            order=order,
            driver=driver,
            leg_type="return",
            status="assigned",
            distance_km=delivery_dist,
            driver_amount=delivery_amount,
            client_fee_share=0,
            fagni_margin=0,
            started_at=None,
            finished_at=None,
        )
    )

    DeliveryLeg.objects.bulk_create(legs_to_create)

    return DeliveryLeg.objects.filter(order=order)


@require_POST
@login_required
def driver_leg_action(request, leg_id, action):
    """
    Action côté livreur sur une jambe de livraison (DeliveryLeg).
    Actions : accept / start / finish / cancel

    Règles :
    - Un livreur ne peut agir que sur SES jambes (sauf admin/staff).
    - Interdit de lancer/terminer une jambe "return/delivery" tant que wash_complete_time n'est pas renseigné.
    - Interdit de démarrer une jambe return/delivery si la jambe pickup n'est pas terminée.
    - Interdit d'avoir 2 jambes in_progress en même temps sur une même commande.
    - Statut global commande recalculé depuis les jambes.
    """
    leg = get_object_or_404(
        DeliveryLeg.objects.select_related("order", "driver"),
        pk=leg_id,
    )
    order = leg.order
    old_status = leg.status
    now = timezone.now()

    # Sécurité : un livreur ne peut agir que sur ses legs
    # (si ton DeliveryPartner n'a pas de lien user, adapte ici)
    if not (request.user.is_staff or request.user.is_superuser):
        # Si DeliveryPartner possède un champ user :
        dp_user = getattr(leg.driver, "user", None)
        if dp_user and dp_user != request.user:
            return HttpResponseForbidden("Accès refusé (ce leg ne t'appartient pas).")

    # Toutes les jambes de la commande
    legs_qs = DeliveryLeg.objects.filter(order=order).order_by("id")
    pickup_legs = legs_qs.filter(leg_type="pickup")

    # Robustesse (legacy)
    DELIVERY_TYPES = {"return", "delivery"}

    def all_pickups_done():
        if not pickup_legs.exists():
            return False
        return not pickup_legs.exclude(status="done").exists()

    def any_leg_in_progress_other_than(current_leg):
        return legs_qs.exclude(pk=current_leg.pk).filter(status="in_progress").exists()

    # ---------------------------------------
    # 🔒 Blocage livraison si linge pas prêt
    # ---------------------------------------
    if leg.leg_type in DELIVERY_TYPES and action in {"accept", "start", "finish"}:
        if not order.wash_complete_time:
            messages.error(
                request,
                "La blanchisserie n'a pas encore confirmé que le linge est prêt. Livraison bloquée."
            )
            return redirect("orders:driver_order_detail", order_id=order.id)

    # ---------------------------------------
    # 🔒 Bloquer start/finish return tant que pickup pas terminé
    # ---------------------------------------
    if pickup_legs.exists() and leg.leg_type != "pickup" and action in {"start", "finish"}:
        if not all_pickups_done():
            messages.error(
                request,
                "Tu dois d'abord terminer la collecte (Client → Blanchisserie) avant de démarrer la livraison."
            )
            return redirect("orders:driver_order_detail", order_id=order.id)

    # ---------------------------------------
    # 🔒 Empêcher deux jambes in_progress
    # ---------------------------------------
    if action == "start" and any_leg_in_progress_other_than(leg):
        messages.warning(
            request,
            "Une autre étape de cette course est déjà en cours. Termine-la avant d'en démarrer une nouvelle."
        )
        return redirect("orders:driver_order_detail", order_id=order.id)

    # =========================
    #   Transitions statut LEG
    # =========================
    valid_actions = {"accept", "start", "finish", "cancel"}
    if action not in valid_actions:
        return HttpResponseBadRequest("Action non reconnue")

    if action == "accept":
        # pending -> assigned
        if leg.status == "pending":
            leg.status = "assigned"

    elif action == "start":
        # pending/assigned -> in_progress
        if leg.status in {"pending", "assigned"}:
            leg.status = "in_progress"
            if not leg.started_at:
                leg.started_at = now

            # timestamps commande
            if leg.leg_type == "pickup" and not order.pickup_time:
                order.pickup_time = now
                order.save(update_fields=["pickup_time"])
            elif leg.leg_type in DELIVERY_TYPES and not order.return_time:
                order.return_time = now
                order.save(update_fields=["return_time"])

    elif action == "finish":
        # assigned/in_progress -> done
        if leg.status in {"assigned", "in_progress"}:
            leg.status = "done"
            if not leg.finished_at:
                leg.finished_at = now

    elif action == "cancel":
        # tout sauf done -> canceled
        if leg.status != "done":
            leg.status = "canceled"
            if not leg.finished_at:
                leg.finished_at = now

    # Si rien n’a changé
    if leg.status == old_status:
        messages.info(request, "Aucune modification de statut pour cette étape.")
        return redirect("orders:driver_order_detail", order_id=order.id)

    # Sauvegarde jambe
    update_fields = ["status"]
    if leg.started_at:
        update_fields.append("started_at")
    if leg.finished_at:
        update_fields.append("finished_at")
    leg.save(update_fields=list(set(update_fields)))

    # =========================
    # Refresh legs + timestamps commande
    # =========================
    legs = list(DeliveryLeg.objects.filter(order=order).values_list("leg_type", "status"))
    statuses = {s for (_, s) in legs}
    pickup_statuses = [s for (t, s) in legs if t == "pickup"]

    DELIVERY_TYPES = {"return", "delivery"}  # simple et propre
    delivery_statuses = [s for (t, s) in legs if t in DELIVERY_TYPES]

    # dropoff_time : quand toutes les pickups sont done
    if pickup_statuses and all(s == "done" for s in pickup_statuses):
        if not order.dropoff_time:
            order.dropoff_time = now
            order.save(update_fields=["dropoff_time"])

    # delivered_time : quand toutes les return/delivery sont done
    if delivery_statuses and all(s == "done" for s in delivery_statuses):
        if not order.delivered_time:
            order.delivered_time = now
            order.save(update_fields=["delivered_time"])

    # =========================
    # Statut global commande
    # =========================
    new_order_status = order.status

    # Si au moins une jambe canceled et aucune jambe active -> on peut annuler
    if statuses and statuses.issubset({"canceled"}):
        new_order_status = "canceled"
    elif statuses == {"done"}:
        new_order_status = "done"
    elif "in_progress" in statuses or "done" in statuses or "assigned" in statuses:
        new_order_status = "in_progress"
    else:
        new_order_status = "pending"

    if new_order_status != order.status:
        order.status = new_order_status
        order.save(update_fields=["status"])

    messages.success(request, "Statut mis à jour.")
    return redirect("orders:driver_order_detail", order_id=order.id)


from django.http import JsonResponse  # <-- si pas déjà importé

@login_required
def driver_order_live_status(request, order_id):
    """
    Lot 4.4 — Endpoint JSON pour rafraîchir la vue livreur sans reload.
    Retourne statut commande + timestamps + legs (type/status/montants/distance).

    Sécurité :
    - staff/superuser OK
    - sinon : l'utilisateur doit être le propriétaire d'au moins un leg de cette commande
      (si DeliveryPartner a un champ user)
    """
    order = get_object_or_404(Order.objects.select_related("laundry_partner", "customer"), pk=order_id)

    legs_qs = DeliveryLeg.objects.filter(order=order).select_related("driver").order_by("id")

    # --- sécurité : user doit être le driver (si modèle le permet)
    if not (request.user.is_staff or request.user.is_superuser):
        # si ton DeliveryPartner a un champ user
        allowed = False
        for leg in legs_qs[:10]:  # petit cut pour éviter boucle énorme (normalement peu de legs)
            dp_user = getattr(leg.driver, "user", None)
            if dp_user and dp_user == request.user:
                allowed = True
                break
        if not allowed and legs_qs.exists():
            # si on ne peut pas vérifier user (dp_user None partout), on laisse passer
            # MAIS si dp_user existe et ne match pas => refus
            any_dp_user = any(getattr(l.driver, "user", None) is not None for l in legs_qs)
            if any_dp_user:
                return HttpResponseForbidden("Accès refusé.")

    def dt(v):
        return v.isoformat() if v else None

    legs = []
    for leg in legs_qs:
        legs.append({
            "id": leg.id,
            "leg_type": leg.leg_type,
            "status": leg.status,
            "distance_km": float(leg.distance_km or 0),
            "driver_amount": float(getattr(leg, "driver_amount", 0) or 0),
            "started_at": dt(getattr(leg, "started_at", None)),
            "finished_at": dt(getattr(leg, "finished_at", None)),
        })

    payload = {
        "ok": True,
        "order": {
            "id": order.id,
            "code": getattr(order, "code", None),
            "status": order.status,
            "created_at": dt(getattr(order, "created_at", None)),
            "pickup_time": dt(getattr(order, "pickup_time", None)),
            "dropoff_time": dt(getattr(order, "dropoff_time", None)),
            "wash_complete_time": dt(getattr(order, "wash_complete_time", None)),
            "return_time": dt(getattr(order, "return_time", None)),
            "delivered_time": dt(getattr(order, "delivered_time", None)),
        },
        "legs": legs,
        "server_time": dt(timezone.now()),
    }
    return JsonResponse(payload)


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


@login_required
def driver_map(request):
    """
    Carte des livreurs FAGNI :
    - Affiche les livreurs actifs avec coordonnées.
    - Envoie les données JSON pour Leaflet.
    """

    from decimal import Decimal
    from django.db.models import Sum

    # Période de la semaine courante
    today = timezone.localdate()
    start_week = today - timezone.timedelta(days=today.weekday())

    # On ne garde que les livreurs actifs avec latitude & longitude renseignées
    drivers_qs = DeliveryPartner.objects.filter(
        is_active=True,
        latitude__isnull=False,
        longitude__isnull=False,
    ).order_by("name")

    drivers = []

    for d in drivers_qs:
        # Commandes de la semaine pour ce livreur
        week_orders = Order.objects.filter(
            delivery_partner=d,
            created_at__date__gte=start_week,
            created_at__date__lte=today,
        )

        week_orders_count = week_orders.count()
        week_earnings = week_orders.aggregate(
            total=Sum("amount_driver_partner")
        )["total"] or Decimal("0")

        # Conversion des coordonnées en float pour Leaflet
        try:
            lat = float(d.latitude)
            lng = float(d.longitude)
        except (TypeError, ValueError):
            # Si on n'arrive pas à convertir, on skip ce livreur
            continue

        drivers.append({
            "id": d.id,
            "name": d.name,
            "city": getattr(d, "city", "") or "",
            "latitude": lat,
            "longitude": lng,
            "week_orders": week_orders_count,
            # on convertit en int pour éviter les Decimals en JSON
            "week_earnings": int(week_earnings),
        })

    # JSON pour le JS (Leaflet)
    drivers_json = json.dumps(drivers, ensure_ascii=False)

    context = {
        "drivers": drivers,  # utilisé dans la colonne de droite
        "drivers_json": drivers_json,  # utilisé par le <script id="drivers-json">
        "connected_driver": get_connected_driver(request),
    }
    return render(request, "orders/driver_map.html", context)


@require_GET
@login_required
def driver_map_data(request):
    """
    Endpoint JSON pour rafraîchir la carte des livreurs (Leaflet).
    Utilisé par driver_map.html en auto-refresh.
    """
    from decimal import Decimal
    from django.db.models import Sum

    today = timezone.localdate()
    start_week = today - timezone.timedelta(days=today.weekday())

    drivers_qs = DeliveryPartner.objects.filter(
        is_active=True,
        latitude__isnull=False,
        longitude__isnull=False,
    ).order_by("name")

    drivers = []

    for d in drivers_qs:
        week_orders = Order.objects.filter(
            delivery_partner=d,
            created_at__date__gte=start_week,
            created_at__date__lte=today,
        )

        week_orders_count = week_orders.count()
        week_earnings = week_orders.aggregate(total=Sum("amount_driver_partner"))["total"] or Decimal("0")

        # Dernière commande active (optionnel, utile OPS)
        active_order = (
            Order.objects.filter(delivery_partner=d, status__in=["pending", "in_progress"])
            .order_by("-created_at")
            .first()
        )

        try:
            lat = float(d.latitude)
            lng = float(d.longitude)
        except (TypeError, ValueError):
            continue

        drivers.append({
            "id": d.id,
            "name": d.name,
            "city": getattr(d, "city", "") or "",
            "latitude": lat,
            "longitude": lng,
            "week_orders": int(week_orders_count),
            "week_earnings": int(week_earnings),
            "active_order_id": active_order.id if active_order else None,
            "active_order_code": getattr(active_order, "code", None) if active_order else None,
            "updated_at": getattr(d, "updated_at", None).isoformat() if getattr(d, "updated_at", None) else None,
        })

    return JsonResponse({"drivers": drivers})

