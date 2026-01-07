from __future__ import annotations
from functools import wraps
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timedelta, time
from django.template.loader import render_to_string
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods
from django.db.models import (
    Count,
    Q,
    Sum,
    F,
    Value,
    Max,
    DecimalField,
    ExpressionWrapper,
    Case,
    When,
)
from django.db import transaction
from wallets.models import WalletTransaction
from wallets.services import (
    get_or_create_wallet_for_delivery_partner,
    credit_wallet,
    distribute_order_revenues,
)
from django.db.models.functions import Coalesce, Cast, TruncDate
from django.views.decorators.http import require_POST, require_http_methods
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from django.utils.dateparse import parse_date
from django.utils.encoding import smart_str

from orders.utils.pricing import compute_order_amounts
from orders.utils.settings_loader import get_pricing_settings
from orders.utils.geocoding import ensure_order_geocoded
from orders.utils.geo import resolve_pickup_coords, resolve_delivery_coords, resolve_provider_coords
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
from partners.models import LaundryPartner, DeliveryPartner
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from orders.utils.assign import get_active_drivers
from weasyprint import HTML
import uuid
import csv
import base64
import json
import re
import logging


DEC12 = DecimalField(max_digits=12, decimal_places=2)
DEC = DecimalField(max_digits=12, decimal_places=2)
DECIMAL_ZERO = Decimal("0")


logger = logging.getLogger(__name__)
# =========================
#  Helpers AJAX / Client auth
# =========================

CLIENT_PHONE_COOKIE = "client_phone"


def _is_json_request(request) -> bool:
    # AJAX (fetch) + Accept JSON
    xrw = (request.headers.get("X-Requested-With") or "").lower()
    accept = (request.headers.get("Accept") or "").lower()
    return xrw == "xmlhttprequest" or "application/json" in accept


def _client_phone(request) -> str | None:
    """
    Retourne le téléphone client "authentifié" côté app client.
    Stratégie (simple) : cookie HTTPOnly.
    """
    phone = request.COOKIES.get(CLIENT_PHONE_COOKIE)
    if phone:
        phone = phone.strip()
    return phone or None


def client_login_required(view_func):
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not _client_phone(request):
            # ✅ IMPORTANT: pour les appels JS, JSON 401 (pas redirect HTML)
            if _is_json_request(request):
                return JsonResponse({"ok": False, "error": "not_authenticated"}, status=401)
            return redirect("orders:client_login")
        return view_func(request, *args, **kwargs)
    return _wrapped
# Aliases compat (anciens noms)
client_required = client_login_required
_client_required = client_login_required


# =========================
#  Login "client" (par téléphone)
# =========================
@require_http_methods(["GET", "POST"])
def client_login(request):
    """
    Login client minimal: on saisit un téléphone => pose cookie client_phone.
    (Tu peux durcir plus tard: OTP, code SMS, etc.)
    """
    if request.method == "GET":
        return render(request, "orders/client_login.html", {"ok": True})


    phone = (request.POST.get("phone") or "").strip()
    if not phone:
        # en JSON si AJAX
        if _is_json_request(request):
            return JsonResponse({"ok": False, "error": "missing_phone"}, status=400)
        return render(request, "orders/client_login.html", {"ok": False, "error": "missing_phone"})

    resp = redirect("orders:client_home")  # adapte si tu as un autre nom de route
    # cookie 7 jours, HTTPOnly (safe), SameSite Lax
    resp.set_cookie(
        CLIENT_PHONE_COOKIE,
        phone,
        max_age=7 * 24 * 3600,
        httponly=True,
        samesite="Lax",
    )
    return resp


@require_http_methods(["POST"])
def client_logout(request):
    resp = redirect("orders:client_login")
    resp.delete_cookie(CLIENT_PHONE_COOKIE)
    return resp


# =========================
#  LIVE endpoint (exemple)
# =========================
@client_login_required
def client_order_live_status(request, order_id: int):
    """
    Endpoint JSON live pour le suivi commande client.
    Règle d'accès: le cookie client_phone doit matcher le customer.phone (ou autre logique).
    """
    from orders.models import Order

    phone = _client_phone(request)

    o = (
        Order.objects
        .select_related("customer")
        .prefetch_related("items", "legs")
        .filter(pk=order_id)
        .first()
    )
    if not o:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)

    customer_phone = ((getattr(o.customer, "phone", "") or "")).strip()
    if not customer_phone or not phone or customer_phone != phone:
        return JsonResponse({"ok": False, "error": "forbidden"}, status=403)

    items = []
    for it in o.items.all():
        qty = float(it.quantity or 0)
        price = float(it.unit_price or 0)
        items.append({
            "id": it.id,
            "designation": it.designation,
            "quantity": qty,
            "unit_price": price,
            "total": float(getattr(it, "line_total", qty * price)),
        })

    legs = []
    for leg in o.legs.all().order_by("id"):
        legs.append({
            "id": leg.id,
            "leg_type": leg.leg_type,
            "status": leg.status,
            "driver_amount": float(leg.driver_amount or 0),
            "driver_id": leg.driver_id,
        })

    # ✅ Source unique de vérité pour les montants
    pricing = _compute_order_pricing(o)

    amounts = {
        "prestation_total": float(pricing["items_total"]),
        "service_fee": float(pricing["service_fee"]),
        "delivery_fee": float(pricing["delivery_fee"]),
        "express_extra_fee": float(pricing.get("express_extra_fee", 0)),
        "vat_fagni": float(pricing["vat_fagni"]),
        "total_ttc": float(pricing["total_client"]),
    }

    return JsonResponse({
        "ok": True,
        "order": {
            "id": o.id,
            "code": o.code,
            "status": o.status,
            "payment_status": o.payment_status,
            "items": items,
            "pickup_time": o.pickup_time.isoformat() if o.pickup_time else None,
            "wash_complete_time": o.wash_complete_time.isoformat() if o.wash_complete_time else None,
            "delivered_time": o.delivered_time.isoformat() if o.delivered_time else None,
            "express_extra_fee": float(pricing.get("express_extra_fee", 0)),
        },
        "amounts": amounts,
        "legs": legs,
    })


def _wallet_net_expr():
    """
    Net = somme(direction=in) - somme(direction=out)
    Utilisé pour revenus livreurs basés sur WalletTransaction.
    """
    return Sum(
        Case(
            When(direction="in", then=F("amount")),
            When(direction="out", then=-F("amount")),
            default=Value(0),
            output_field=DEC,
        )
    )


@login_required
def driver_app_alias(request):
    qs = request.META.get("QUERY_STRING", "")
    url = reverse("orders:driver_app")  # => /orders/driver-app/
    return redirect(f"{url}?{qs}" if qs else url)

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


def _qr_png_base64(data: str) -> str:
    """
    Génère un QR code PNG et renvoie le base64 (sans préfixe data:).
    """
    try:
        import qrcode
    except Exception:
        return ""

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=6,
        border=2,
    )
    qr.add_data(data or "")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


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

            # Auto-heal legs (évite commandes "cassées" sans legs)
            try:
                if o.delivery_partner_id and (o.delivery_fee or 0) > 0 and not o.legs.exists():
                    from orders.models import sync_delivery_legs_for_order
                    sync_delivery_legs_for_order(o)
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
            age_h = _hours(o.created_at, timezone.now()) or 0
            if age_h >= SLA_PICKUP_H:
                reason = f"Collecte en retard (>{SLA_PICKUP_H}h)"
                next_step = "pickup"

        if o.status == "in_progress":
            if o.pickup_time and not o.dropoff_time:
                age_h = _hours(o.pickup_time, timezone.now()) or 0
                if age_h >= SLA_DROPOFF_H:
                    reason = f"Dépôt blanchisserie en retard (>{SLA_DROPOFF_H}h après collecte)"
                    next_step = "dropoff"

            elif o.dropoff_time and not o.wash_complete_time:
                age_h = _hours(o.dropoff_time, timezone.now()) or 0
                if age_h >= SLA_WASH_H:
                    reason = f"Lavage trop long (>{SLA_WASH_H}h après dépôt)"
                    next_step = "wash_done"

            elif o.wash_complete_time and not o.return_time:
                age_h = _hours(o.wash_complete_time, timezone.now()) or 0
                if age_h >= SLA_RETURN_H:
                    reason = f"Reprise livreur en retard (>{SLA_RETURN_H}h après lavage)"
                    next_step = "return"

            elif o.return_time and not o.delivered_time:
                age_h = _hours(o.return_time, timezone.now()) or 0
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
        delta = (timezone.now() - d.updated_at).total_seconds()
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
        "drivers_list": drivers_list,
        "reset_url": reset_url,
        "displayed_total_count": displayed_total_count,
    }
    return render(request, "orders/ops_dashboard.html", context)


@require_POST
@login_required
def ops_update_step(request, order_id, action):
    """
    Met à jour les timestamps opérationnels :
    - pickup, dropoff, wash_done, return, delivered
    + bascule éventuellement le statut et les legs.

    Sécurité :
    - empêche les étapes dans le mauvais ordre
    - empêche toute étape OPS si la commande n'est pas chiffrée
      (au moins 1 item + total_client_ttc > 0, et si livreur => delivery_fee > 0)
    """
    order = get_object_or_404(Order, pk=order_id)

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

    # Déjà fait ?
    if getattr(order, field_name, None):
        messages.warning(request, f"Déjà fait : {label}.")
        return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")

    # Ordre des étapes
    missing = [f for f in prerequisites[action] if not getattr(order, f, None)]
    if missing:
        messages.error(request, "Impossible : étape précédente non validée.")
        return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")

    # ------------------------------------------------------------
    # 1) 🔁 SYNC FINANCIER (DB doit être la source de vérité)
    # ------------------------------------------------------------
    try:
        order.compute_totals(save=True)  # update_financials(save=True)
    except Exception:
        # On ne bloque pas si le recalcul plante, mais on traitera comme non chiffré
        pass

    # ------------------------------------------------------------
    # 2) 🔒 GARDE-FOU : commande doit être chiffrée
    # ------------------------------------------------------------
    has_items = False
    try:
        has_items = order.items.exists()
    except Exception:
        has_items = False

    total_client = getattr(order, "total_client_ttc", None)
    if total_client is None:
        total_client = getattr(order, "total", None)
    total_client = total_client or Decimal("0")
    if not isinstance(total_client, Decimal):
        try:
            total_client = Decimal(str(total_client))
        except Exception:
            total_client = Decimal("0")

    delivery_fee = getattr(order, "delivery_fee", None) or Decimal("0")
    if not isinstance(delivery_fee, Decimal):
        try:
            delivery_fee = Decimal(str(delivery_fee))
        except Exception:
            delivery_fee = Decimal("0")

    has_driver = bool(order.delivery_partner_id)

    if not has_items:
        messages.error(
            request,
            "Commande non chiffrée (aucun article / prestation). Ajoute au moins 1 prestation avant de valider l’OPS."
        )
        return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")

    if total_client <= 0:
        messages.error(
            request,
            "Commande non chiffrée (total client = 0). Ajoute au moins 1 prestation / recalcule les montants."
        )
        return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")

    if has_driver and delivery_fee <= 0:
        messages.error(
            request,
            "Commande non chiffrée (livreur assigné mais frais de livraison = 0). Recalcule/ajuste la livraison."
        )
        return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")

    if (not has_driver) and delivery_fee > 0:
        messages.error(
            request,
            "Commande incohérente (frais de livraison > 0 sans livreur). Assigne un livreur ou remets la livraison à 0."
        )
        return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")

    # ------------------------------------------------------------
    # 3) (optionnel) sync legs si besoin (après recalcul)
    # ------------------------------------------------------------
    try:
        from orders.models import DeliveryLeg, sync_delivery_legs_for_order
        if not DeliveryLeg.objects.filter(order=order).exclude(status="canceled").exists():
            sync_delivery_legs_for_order(order)
    except Exception:
        pass

    # ------------------------------------------------------------
    # 4) ✅ VALIDER L’ÉTAPE
    # ------------------------------------------------------------
    setattr(order, field_name, timezone.now())

    # Status + legs
    if action == "pickup":
        if order.status == "pending":
            order.status = "in_progress"

        for leg in order.legs.filter(leg_type="pickup"):
            update_leg_status(leg, "start", user=request.user)

        for leg in order.legs.filter(leg_type="return").exclude(status="done"):
            if leg.status == "pending":
                update_leg_status(leg, "accept", user=request.user)

    elif action == "dropoff":
        for leg in order.legs.filter(leg_type="pickup"):
            update_leg_status(leg, "finish", user=request.user)

        for leg in order.legs.filter(leg_type="return").exclude(status="done"):
            if leg.status == "pending":
                update_leg_status(leg, "accept", user=request.user)

    elif action == "wash_done":
        if order.delivery_partner:
            for leg in order.legs.filter(
                leg_type="return",
                status="pending",
            ):
                update_leg_status(leg, "accept", user=request.user)

    elif action == "return":
        for leg in order.legs.filter(leg_type="return"):
            update_leg_status(leg, "start", user=request.user)

    elif action == "delivered":
        order.status = "done"
        for leg in order.legs.exclude(status="done"):
            update_leg_status(leg, "finish", user=request.user)

    # MLM à la livraison
    if action == "delivered":
        try:
            if (not getattr(order, "mlm_distributed", False)) and (order.service_fee or 0) > 0:
                order.distribute_mlm_commissions()
        except Exception:
            pass

    update_fields = {field_name, "status", "updated_at"}
    order.save(update_fields=list(update_fields))

    messages.success(request, label)
    return redirect(f"{reverse('orders:ops_dashboard')}?highlight={order.id}")


@login_required
@require_POST
def order_mark_paid(request, order_id):
    """
    ✅ Encaisser (Back-office)
    - Marque la commande payée
    - Aligne amount_paid sur la source unique (_build_invoice_context)
    - Verrouille (via status paiement + UI) ; évite double encaissement
    - Distribue revenus : blanchisserie + wallet interne (distribute_order_revenues)
    - Déclenche payouts livreur UNIQUEMENT pour les legs déjà done (anti-doublon par leg)
    - Met FNE en pending (si activée / pas déjà envoyée)
    """

    if not request.user.is_staff:
        return HttpResponseForbidden("Accès refusé.")

    order = get_object_or_404(
        Order.objects.select_related("customer", "laundry_partner", "delivery_partner"),
        pk=order_id
    )

    # Déjà payée => rien à faire
    if getattr(order, "payment_status", None) == "paid":
        messages.info(request, "Commande déjà payée.")
        return redirect("orders:detail", order_id=order.id)

    # Source unique montants (canon)
    ctx = _build_invoice_context(order)
    total_ttc_client = ctx.get("total_ttc_client") or 0

    # Saisie optionnelle
    method = (request.POST.get("payment_method") or "").strip() or None
    ref = (request.POST.get("payment_reference") or "").strip() or None

    # 1) Marquer payé
    order.payment_status = "paid"
    try:
        # total_ttc_client peut être Decimal
        order.amount_paid = total_ttc_client
    except Exception:
        pass
    order.payment_date = timezone.now()

    if method is not None:
        order.payment_method = method
    if ref is not None:
        order.payment_reference = ref

    # 2) Facture / numéros / financials (si ton update_financials gère invoice_number)
    try:
        order.update_financials(save=True)
    except Exception:
        # au pire, on sauvegarde quand même le paiement
        order.save()

    # 3) FNE : passer en pending si activée (et pas déjà "sent/accepted/rejected/error")
    try:
        current_fne = getattr(order, "fne_status", None) or "disabled"
        if current_fne not in ("disabled", "sent", "accepted", "rejected", "error"):
            order.fne_status = "pending"
            order.save(update_fields=["fne_status", "updated_at"])
    except Exception:
        pass

    # 4) Distribution des revenus (blanchisserie + wallet interne)
    #    (anti-doublon via order.wallets_distributed + contraintes uniques côté tx)
    try:
        distribute_order_revenues(order, recompute=True, force=False)
    except Exception as e:
        # On ne bloque pas l'encaissement si distribution échoue, mais on le signale
        messages.warning(request, f"Paiement OK, mais distribution wallets incomplète : {e}")

    # 5) Payouts livreur : UNIQUEMENT legs déjà done (si le paiement arrive après)
    try:
        done_legs = DeliveryLeg.objects.filter(order=order, status="done").select_related("driver")
        count = 0
        for leg in done_legs:
            tx = trigger_driver_payout_for_leg(leg)
            if tx:
                count += 1
        if count:
            messages.success(request, f"Paiement OK. Payout livreur déclenché sur {count} jambe(s) terminée(s).")
        else:
            messages.success(request, "Paiement OK. Aucun payout livreur à déclencher (ou déjà payé).")
    except Exception as e:
        messages.warning(request, f"Paiement OK, mais payout livreur non déclenché : {e}")

    return redirect("orders:detail", order_id=order.id)


@login_required
def ops_drivers_live(request):
    """
    Lot 4.9 — JSON LIVE pour la carte Leaflet du OPS Dashboard.
    Retourne la liste des DeliveryPartner actifs avec latitude/longitude + stats semaine.
    + updated_at + server_time
    """
    today = timezone.localdate()
    start_week = today - timedelta(days=today.weekday())

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

    payload = []
    for d in drivers_qs:
        st = stats_map.get(d.id, {"week_orders": 0, "week_earnings": 0})

        updated_at = None
        if getattr(d, "updated_at", None):
            try:
                updated_at = timezone.localtime(d.updated_at).isoformat()
            except Exception:
                updated_at = None

        payload.append(
            {
                "id": d.id,
                "name": getattr(d, "name", "") or "",
                "phone": getattr(d, "phone", "") or "",
                "lat": float(d.latitude),
                "lng": float(d.longitude),
                "is_active": bool(getattr(d, "is_active", True)),
                "updated_at": updated_at,
                "week_orders": st["week_orders"],
                "week_earnings": st["week_earnings"],
            }
        )

    return JsonResponse(
        {
            "drivers": payload,
            "count": len(payload),
            "server_time": timezone.localtime(timezone.now()).isoformat(),
        }
    )


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
    Export Excel du dashboard financier – SOURCE DE VÉRITÉ :
    compute_order_amounts() + TVA sur fagni_revenue_ht.
    - Onglet 1 : Synthèse
    - Onglet 2 : Détail (mêmes colonnes que dashboard)
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

    raw_orders = list(qs[:500])

    cfg = get_pricing_settings()
    vat_rate = _get_vat_rate_percent(cfg)

    def q(x):
        try:
            return _q(x)
        except Exception:
            try:
                return Decimal(str(x))
            except Exception:
                return Decimal("0")

    # Filtre montant mini
    try:
        min_amount = Decimal(min_amount_input) if min_amount_input else Decimal("0")
    except Exception:
        min_amount = Decimal("0")

    rows = []
    total_ca = Decimal("0")
    total_prestations = Decimal("0")
    total_service = Decimal("0")
    total_delivery = Decimal("0")
    total_express = Decimal("0")
    total_vat_fagni = Decimal("0")
    total_fagni_ttc = Decimal("0")
    total_logistic_margin = Decimal("0")
    total_laundry = Decimal("0")
    total_driver = Decimal("0")

    for o in raw_orders:
        amounts = compute_order_amounts(o)

        subtotal = q(amounts.get("subtotal", 0))
        service_fee_ht = q(amounts.get("service_fee_ht", 0))
        delivery_fee_client = q(amounts.get("delivery_fee_client", 0))
        express = q(
            amounts.get("express_for_client", 0)
            or amounts.get("express_extra_fee_client", 0)
            or amounts.get("express_fee_client", 0)
            or getattr(o, "express_extra_fee", 0)
            or 0
        )

        fagni_revenue_ht = q(amounts.get("fagni_revenue_ht", 0))
        vat_fagni = q((fagni_revenue_ht * q(vat_rate)) / Decimal("100"))

        total_ht_client = q(subtotal + service_fee_ht + delivery_fee_client + express)
        total_ttc_client = q(total_ht_client + vat_fagni)

        logistic_margin = q(
            amounts.get("logistic_margin", 0)
            or getattr(o, "logistic_margin", 0)
            or 0
        )
        fagni_ttc = q(fagni_revenue_ht + vat_fagni)

        amount_laundry = q(
            amounts.get("amount_laundry_partner", 0)
            or getattr(o, "amount_laundry_partner", 0)
            or 0
        )
        amount_driver = q(
            amounts.get("amount_driver_partner", 0)
            or getattr(o, "amount_driver_partner", 0)
            or amounts.get("driver_income", 0)
            or getattr(o, "driver_logistic_cost", 0)
            or 0
        )

        # filtres statut paiement "export"
        paid = q(getattr(o, "amount_paid", 0))
        due = q(getattr(o, "amount_due", 0))
        is_fully_paid = (due <= 0)

        if status_filter == "paid" and not is_fully_paid:
            continue
        if status_filter == "partial" and not (paid > 0 and due > 0):
            continue
        if status_filter == "unpaid" and not (paid == 0 and due > 0):
            continue

        if min_amount > 0 and total_ttc_client < min_amount:
            continue

        rows.append({
            "date": o.created_at,
            "code": o.code or str(o.id),
            "client": (o.customer.name if o.customer else ""),
            "laundry": (o.laundry_partner.name if o.laundry_partner else ""),
            "payment_status": o.payment_status or "",
            "total_client": total_ttc_client,
            "prestations": subtotal,
            "service_fee": service_fee_ht,
            "delivery": delivery_fee_client,
            "express": express,
            "vat_fagni": vat_fagni,
            "fagni_ttc": fagni_ttc,
            "logistic_margin": logistic_margin,
            "amount_laundry": amount_laundry,
            "amount_driver": amount_driver,
            "partners_total": q(amount_laundry + amount_driver),
            "paid": paid,
            "due": due,
        })

        total_ca += total_ttc_client
        total_prestations += subtotal
        total_service += service_fee_ht
        total_delivery += delivery_fee_client
        total_express += express
        total_vat_fagni += vat_fagni
        total_fagni_ttc += fagni_ttc
        total_logistic_margin += logistic_margin
        total_laundry += amount_laundry
        total_driver += amount_driver

    # ---------- Excel ----------
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Synthèse"

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
    right = Alignment(horizontal="right", vertical="center")
    left = Alignment(horizontal="left", vertical="center")

    # Titre
    ws1["A1"] = "FAGNI – Dashboard financier (export)"
    ws1["A1"].font = title_font
    ws1["A1"].fill = header_fill
    ws1.merge_cells("A1:D1")

    ws1["A2"] = "Période"
    ws1["A2"].font = label_font
    ws1["B2"] = f"{date_from or '—'} → {date_to or '—'}"

    ws1["A4"] = "KPI"
    ws1["A4"].font = header_font
    ws1["A4"].fill = section_fill
    ws1.merge_cells("A4:D4")

    kpis = [
        ("Nb commandes", len(rows)),
        ("CA total client (TTC)", total_ca),
        ("Prestations (HT)", total_prestations),
        ("Service fee (HT)", total_service),
        ("Livraison client", total_delivery),
        ("Express", total_express),
        ("TVA FAGNI", total_vat_fagni),
        ("Revenu FAGNI (TTC)", total_fagni_ttc),
        ("Marge logistique", total_logistic_margin),
        ("Montant blanchisserie", total_laundry),
        ("Montant livreur", total_driver),
        ("Total partenaires", total_laundry + total_driver),
    ]

    r = 5
    for label, val in kpis:
        ws1[f"A{r}"] = label
        ws1[f"A{r}"].font = label_font
        ws1[f"B{r}"] = float(val) if isinstance(val, Decimal) else val
        ws1[f"B{r}"].alignment = right
        r += 1

    ws1.column_dimensions["A"].width = 28
    ws1.column_dimensions["B"].width = 22

    # Onglet détail
    ws2 = wb.create_sheet("Commandes")
    headers = [
        "Date", "Code", "Client", "Blanchisserie", "Paiement",
        "Total client TTC", "Prestations HT", "Service fee HT", "Livraison", "Express",
        "TVA FAGNI", "FAGNI TTC", "Marge logistique",
        "Montant blanchisserie", "Montant livreur", "Total partenaires",
        "Payé", "Reste dû",
    ]
    ws2.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws2.cell(row=1, column=c)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = thin_border

    for row in rows:
        ws2.append([
            row["date"].strftime("%d/%m/%Y %H:%M") if row["date"] else "",
            row["code"],
            row["client"],
            row["laundry"],
            row["payment_status"],
            float(row["total_client"]),
            float(row["prestations"]),
            float(row["service_fee"]),
            float(row["delivery"]),
            float(row["express"]),
            float(row["vat_fagni"]),
            float(row["fagni_ttc"]),
            float(row["logistic_margin"]),
            float(row["amount_laundry"]),
            float(row["amount_driver"]),
            float(row["partners_total"]),
            float(row["paid"]),
            float(row["due"]),
        ])

    # tailles
    widths = [18, 14, 22, 22, 12, 16, 14, 14, 12, 12, 12, 12, 14, 18, 14, 16, 12, 12]
    for i, w in enumerate(widths, start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # response
    response = HttpResponse(
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    filename = "FAGNI_finance_dashboard.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    wb.save(response)
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

    service_fee = _dec_or_zero(getattr(order, "service_fee", 0))
    delivery_fee = _dec_or_zero(getattr(order, "delivery_fee", 0))
    vat_fagni = _dec_or_zero(getattr(order, "vat_fagni", 0))
    express_extra_fee = _dec_or_zero(getattr(order, "express_extra_fee", 0))

    # total calculé canonique
    computed_total = items_total + service_fee + delivery_fee + vat_fagni + express_extra_fee

    # total stocké DB (peut être faux)
    stored_total = _dec_or_zero(getattr(order, "total_client_ttc", 0))

    # ✅ DB utilisée seulement si cohérente (±1 FCFA)
    if stored_total > 0 and abs(stored_total - computed_total) <= Decimal("1"):
        total_client = stored_total
    else:
        total_client = computed_total

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
        "express_extra_fee": express_extra_fee,
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
    Dashboard financier FAGNI – Source de vérité :
    - compute_order_amounts() + TVA sur fagni_revenue_ht
    - évite les agrégations DB incohérentes (total_client_ttc / total / etc.)
    """
    payment_filter = request.GET.get("payment", "all")

    qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .order_by("-created_at")
    )

    if payment_filter == "paid":
        qs = qs.filter(payment_status="paid")
    elif payment_filter == "unpaid":
        qs = qs.exclude(payment_status="paid")

    # on limite l'affichage, mais les KPI doivent être calculés sur un volume raisonnable
    # (tu peux monter à 500 si besoin)
    orders = list(qs[:500])

    cfg = get_pricing_settings()
    vat_rate = _get_vat_rate_percent(cfg)

    def q(x):
        try:
            return _q(x)
        except Exception:
            try:
                return Decimal(str(x))
            except Exception:
                return Decimal("0")

    ca_client_total = Decimal("0")
    partners_laundry_total = Decimal("0")
    partners_driver_total = Decimal("0")
    fagni_revenue_total = Decimal("0")      # TTC
    logistic_margin_total = Decimal("0")
    service_fee_total = Decimal("0")

    enriched = []

    for o in orders:
        amounts = compute_order_amounts(o)

        subtotal = q(amounts.get("subtotal", 0))
        service_fee_ht = q(amounts.get("service_fee_ht", 0))
        delivery_fee_client = q(amounts.get("delivery_fee_client", 0))

        express = q(
            amounts.get("express_for_client", 0)
            or amounts.get("express_extra_fee_client", 0)
            or amounts.get("express_fee_client", 0)
            or getattr(o, "express_extra_fee", 0)
            or 0
        )

        fagni_revenue_ht = q(amounts.get("fagni_revenue_ht", 0))
        vat_fagni = q((fagni_revenue_ht * q(vat_rate)) / Decimal("100"))

        total_ht_client = q(subtotal + service_fee_ht + delivery_fee_client + express)
        total_ttc_client = q(total_ht_client + vat_fagni)

        amount_laundry = q(
            amounts.get("amount_laundry_partner", 0)
            or getattr(o, "amount_laundry_partner", 0)
            or 0
        )
        amount_driver = q(
            amounts.get("amount_driver_partner", 0)
            or getattr(o, "amount_driver_partner", 0)
            or amounts.get("driver_income", 0)
            or getattr(o, "driver_logistic_cost", 0)
            or 0
        )

        logistic_margin = q(
            amounts.get("logistic_margin", 0)
            or getattr(o, "logistic_margin", 0)
            or 0
        )

        fagni_revenue_ttc = q(fagni_revenue_ht + vat_fagni)
        partners_total = q(amount_laundry + amount_driver)

        # stocke sur l'objet pour le template
        o.fin_total_client = total_ttc_client
        o.fin_subtotal = subtotal
        o.fin_service_fee = service_fee_ht
        o.fin_delivery_fee = delivery_fee_client
        o.fin_express = express
        o.fin_vat_fagni = vat_fagni
        o.fin_fagni_ttc = fagni_revenue_ttc
        o.fin_amount_laundry = amount_laundry
        o.fin_amount_driver = amount_driver
        o.fin_partners_total = partners_total
        o.fin_logistic_margin = logistic_margin

        ca_client_total += total_ttc_client
        partners_laundry_total += amount_laundry
        partners_driver_total += amount_driver
        fagni_revenue_total += fagni_revenue_ttc
        logistic_margin_total += logistic_margin
        service_fee_total += service_fee_ht

        enriched.append(o)

    order_count = len(enriched)
    avg_ticket = (ca_client_total / Decimal(order_count)).quantize(Decimal("0.01")) if order_count else Decimal("0.00")
    partners_total = partners_laundry_total + partners_driver_total

    context = {
        "payment_filter": payment_filter,
        "orders": enriched[:50],  # affichage seulement

        "ca_client_total": ca_client_total,
        "order_count": order_count,
        "avg_ticket": avg_ticket,

        "partners_total": partners_total,
        "partners_laundry_total": partners_laundry_total,
        "partners_driver_total": partners_driver_total,

        "fagni_revenue_total": fagni_revenue_total,
        "logistic_margin_total": logistic_margin_total,
        "service_fee_total": service_fee_total,

        "vat_rate": vat_rate,  # utile si tu veux l’afficher
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

    # --- LOT E : Blanchisseries géolocalisees (pour estimation front) ---
    laundries_geo = list(
        LaundryPartner.objects.filter(
            is_active=True,
            latitude__isnull=False,
            longitude__isnull=False,
        ).values("id", "name", "latitude", "longitude")
    )
    context["laundries_geo_json"] = json.dumps(laundries_geo, default=str)


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
        customer = Customer.objects.filter(phone=phone).order_by("-id").first()
        if not customer:
            customer = Customer.objects.create(phone=phone, name=name, address=address)

    except Customer.MultipleObjectsReturned:
        customer = (
            Customer.objects.filter(phone=phone)
            .order_by("-id")
            .first()
        )

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
        # IMPORTANT: éviter les "commandes fantômes" en base
        order.delete()
        context["error"] = error
        return render(request, "orders/create.html", context)

    order.save()

    # 1) Géocodage (avant assignation)
    ensure_order_geocoded(order, save=True)

    # 2) Assignation automatique d’une blanchisserie (si autorisée) + raison
    try:
        from orders.assignment import pick_best_laundry
        laundry, laundry_reason = pick_best_laundry(order)
    except Exception:
        laundry, laundry_reason = None, None

    if laundry:
        order.laundry_partner = laundry
        # si tu as un champ reason côté Order un jour, tu pourras le stocker ici
    else:
        # optionnel : si tu as un champ sur Order du genre laundry_partner_unassigned_reason
        # order.laundry_partner_unassigned_reason = laundry_reason
        pass

    # 3) Assignation automatique d’un livreur (si autorisée) + raison
    try:
        from orders.assignment import pick_best_driver
        driver, reason = pick_best_driver(order)
    except Exception:
        driver, reason = None, None

    if driver:
        order.delivery_partner = driver

    # 9) Lignes de commande (PRIX VERROUILLÉS CÔTÉ SERVEUR)
    service_ids = request.POST.getlist("service_id[]")
    designations = request.POST.getlist("designation[]")
    quantities = request.POST.getlist("quantity[]")

    created_any_item = False

    for idx, (sid, desc, qty_str) in enumerate(zip(service_ids, designations, quantities)):
        sid = (sid or "").strip()
        desc = (desc or "").strip()

        # Quantité
        try:
            qty = int(qty_str)
        except Exception:
            qty = 0

        if qty <= 0:
            continue

        # Service (obligatoire pour sécuriser le prix)
        try:
            service_obj = ServiceItem.objects.get(pk=sid)
        except (ServiceItem.DoesNotExist, ValueError, TypeError):
            service_obj = None

        if not service_obj:
            continue

        # Désignation fallback : si vide, on prend le nom catalogue
        if not desc:
            desc = (service_obj.name or "").strip()
            if not desc:
                continue

        # PRIX : on ignore complètement ce que le navigateur a envoyé
        try:
            pu = Decimal(str(service_obj.default_price))
        except Exception:
            pu = Decimal("0")

        if pu <= 0:
            continue

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

        # Photos
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

    # 10) Recalcul financier complet (moteur unique) + distances + legs
    try:
        # ✅ on persiste les montants calculés (express, TVA, parts partenaires, etc.)
        # === SAVE_DELIVERY_FIELDS_BEFORE_FINANCE ===
        # Render/Prod: s'assurer que delivery_mode + delivery_fee sont bien persistés
        try:
            real_fields = {f.name for f in order._meta.fields}
            uf = []
            for f in ('delivery_mode', 'delivery_fee'):
                if f in real_fields:
                    uf.append(f)
            if uf:
                order.save(update_fields=uf)
            else:
                order.save()
        except Exception:
            order.save()
        order.update_financials(save=True)
    except Exception as e:
        print("Erreur update_financials:", e)

    try:
        # distances (si ta méthode existe)
        order.recompute_distances_from_positions()
    except Exception as e:
        print("Erreur recompute_distances_from_positions:", e)

    try:
        from orders.models import DeliveryLeg, sync_delivery_legs_for_order
        if not DeliveryLeg.objects.filter(order=order).exclude(status="canceled").exists():
            sync_delivery_legs_for_order(order)
    except Exception:
        pass

    return redirect("orders:detail", order_id=order.id)


def build_order_finance_context(order):
    """
    Contexte financier "source de vérité" pour l'écran détail commande.
    Aligne detail.html, tickets, et dashboard sur compute_order_amounts().
    """
    cfg = get_pricing_settings()
    amounts = compute_order_amounts(order)

    vat_rate = _get_vat_rate_percent(cfg)

    def q(x):
        try:
            return _q(x)
        except Exception:
            try:
                return Decimal(str(x))
            except Exception:
                return Decimal("0")

    prestation_total = q(amounts.get("subtotal", 0))
    service_fee_ht = q(amounts.get("service_fee_ht", 0))
    delivery_fee_client = q(amounts.get("delivery_fee_client", 0))

    express_surcharge = q(
        amounts.get("express_for_client", 0)
        or amounts.get("express_extra_fee_client", 0)
        or amounts.get("express_fee_client", 0)
        or getattr(order, "express_extra_fee", 0)
        or 0
    )

    fagni_revenue_ht = q(amounts.get("fagni_revenue_ht", 0))
    vat_fagni = q((fagni_revenue_ht * q(vat_rate)) / Decimal("100"))

    total_ht_client = q(prestation_total + service_fee_ht + delivery_fee_client + express_surcharge)
    total_ttc_client = q(total_ht_client + vat_fagni)

    amount_laundry = q(
        amounts.get("amount_laundry_partner", 0)
        or getattr(order, "amount_laundry_partner", 0)
        or 0
    )
    amount_driver = q(
        amounts.get("amount_driver_partner", 0)
        or getattr(order, "amount_driver_partner", 0)
        or amounts.get("driver_income", 0)
        or getattr(order, "driver_logistic_cost", 0)
        or 0
    )

    partners_total = q(amount_laundry + amount_driver)

    margin_delivery = q(
        amounts.get("logistic_margin", 0)
        or getattr(order, "logistic_margin", 0)
        or 0
    )

    return {
        "order": order,

        # Montants client
        "prestation_total": prestation_total,
        "service_fee_ht": service_fee_ht,
        "delivery_fee_client": delivery_fee_client,
        "express_surcharge": express_surcharge,
        "vat_fagni": vat_fagni,
        "total_ttc_client": total_ttc_client,

        # Partenaires
        "amount_laundry": amount_laundry,
        "amount_driver": amount_driver,
        "partners_total": partners_total,

        # FAGNI
        "fagni_revenue_ht": fagni_revenue_ht,
        "margin_delivery": margin_delivery,
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

    ctx_amounts = _build_invoice_context(order)
    context.update(ctx_amounts)  # ajoute amounts + express_for_client etc.
    context["express_client"] = (ctx_amounts.get("amounts") or {}).get("express_for_client", 0)

    return render(request, "orders/detail.html", context)


# -------------------------------------------------------------------
# Helpers Client (session phone)
# -------------------------------------------------------------------

# Clé session canonique (1 seule vérité)
DEFAULT_CLIENT_SESSION_KEY = "fagni_client_phone"

def _client_session_key() -> str:
    # Permet override via settings.py si tu veux
    return getattr(settings, "CLIENT_SESSION_KEY", DEFAULT_CLIENT_SESSION_KEY)

def _normalize_phone(raw: str) -> str:
    """
    Normalisation légère pour éviter les espaces/traits.
    - garde uniquement les chiffres
    - retire 00225 / 225 si présent (CIV)
    """
    s = (raw or "").strip()
    digits = re.sub(r"\D+", "", s)

    # Côte d'Ivoire: +225 / 00225
    if digits.startswith("00225"):
        digits = digits[5:]
    elif digits.startswith("225") and len(digits) > 10:
        digits = digits[3:]

    return digits


# -------------------------------------------------------------------
# Home client (LISTE)  ✅ FIX: on filtre par phone (pas par customer_id)
# + UI: total client + paiement (safe fallbacks)
# -------------------------------------------------------------------

def client_home(request):
    """
    Page Accueil Client:
    - Liste des commandes + filtres statut + pagination
    - Totaux robustes (fallback sur items_total)
    - KPI commandes conditionnel selon filtre sélectionné
    """
    from django.core.paginator import Paginator
    from django.db.models import Sum, F, Value, DecimalField
    from django.db.models.functions import Coalesce, Cast

    phone = _client_phone(request)
    if not phone:
        return redirect("orders:client_login")

    customer = Customer.objects.filter(phone=phone).first()

    # Defaults safe
    orders_qs = Order.objects.none()
    orders_count_all = 0
    active_orders_count = 0
    unpaid_orders_count = 0

    if customer:
        items_sum = Coalesce(
            Sum(
                Cast(F("items__quantity"), DecimalField(max_digits=10, decimal_places=2))
                * Cast(F("items__unit_price"), DecimalField(max_digits=10, decimal_places=2)),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            Value(0, output_field=DecimalField(max_digits=12, decimal_places=2)),
        )

        base_qs = (
            Order.objects.filter(customer=customer)
            .order_by("-created_at")
            .annotate(items_total=items_sum)
            .annotate(
                total_client_display=Value(
                    0,
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                )
            )
        )

        # KPI globaux (sans filtre)
        orders_count_all = base_qs.count()
        active_orders_count = base_qs.filter(status__in=["pending", "in_progress"]).count()
        unpaid_orders_count = base_qs.filter(payment_status__in=["pending", "unpaid", "failed"]).count()

        orders_qs = base_qs

    # ---------------------------
    # Filtre statut
    # ---------------------------
    current_status = (request.GET.get("status") or "all").strip().lower()
    valid = {"all", "pending", "in_progress", "done", "canceled"}
    if current_status not in valid:
        current_status = "all"

    filtered_qs = orders_qs
    if current_status != "all":
        filtered_qs = orders_qs.filter(status=current_status)

    # KPI conditionnel selon filtre sélectionné
    kpi_orders_count = orders_count_all if current_status == "all" else filtered_qs.count()

    # ---------------------------
    # Pagination
    # ---------------------------
    per_page = 8
    page_number = request.GET.get("page") or 1

    paginator = Paginator(filtered_qs, per_page)
    page_obj = paginator.get_page(page_number)
    is_paginated = paginator.num_pages > 1

    # Pagination “premium”
    current = page_obj.number
    last = paginator.num_pages
    window = 2
    start = max(2, current - window)
    end = min(last - 1, current + window)
    pages = list(range(start, end + 1)) if last >= 3 else []
    show_left_ellipsis = start > 2
    show_right_ellipsis = end < (last - 1)

    # --- post-traitement : total_client_display canonique pour la page ---
    orders_page = list(page_obj.object_list)
    for o in orders_page:
        pricing = _compute_order_pricing(o)
        o.total_client_display = pricing["total_client"]
        o.express_extra_fee = pricing.get("express_extra_fee", Decimal("0"))

    ctx = {
        "phone": phone,
        "customer": customer,

        # liste paginée
        "orders": orders_page,

        # pagination
        "page_obj": page_obj,
        "is_paginated": is_paginated,
        "pages": pages,
        "show_left_ellipsis": show_left_ellipsis,
        "show_right_ellipsis": show_right_ellipsis,

        # filtre
        "current_status": current_status,

        # KPI
        "orders_count_all": orders_count_all,
        "kpi_orders_count": kpi_orders_count,
        "active_orders_count": active_orders_count,
        "unpaid_orders_count": unpaid_orders_count,
    }
    return render(request, "orders/client_home.html", ctx)


# -------------------------------------------------------------------
# Nouvelle commande client
# -------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
@client_required
def client_new_order(request):
    """
    Création Client V1 (rapide) :
    - téléphone (session)
    - nom + adresse + notes
    -> crée une commande "pending"
    """
    phone = _client_phone(request)
    customer = Customer.objects.filter(phone=phone).order_by("-id").first()

    error = None
    name_init = customer.name if customer else ""
    address_init = getattr(customer, "address", "") if customer else ""

    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        address = (request.POST.get("address") or "").strip()
        notes = (request.POST.get("notes") or "").strip()

        if not name:
            error = "Merci de renseigner ton nom."
        elif not address:
            error = "Merci de renseigner ton adresse."
        else:
            # ✅ update_or_create évite de multiplier les Customer si tu recrées
            customer, _created = Customer.objects.update_or_create(
                phone=phone,
                defaults={"name": name, "address": address},
            )

            # create order
            code = uuid.uuid4().hex[:8].upper()
            while Order.objects.filter(code=code).exists():
                code = uuid.uuid4().hex[:8].upper()

            order = Order.objects.create(
                customer=customer,
                status="pending",
                code=code,
                notes=notes or None,
                pickup_address=address,
                delivery_address=address,
            )

            from orders.models import DeliveryLeg

            DeliveryLeg.objects.get_or_create(order=order, leg_type="pickup", defaults={"status":"pending"})
            DeliveryLeg.objects.get_or_create(order=order, leg_type="return", defaults={"status":"pending"})

            # ----------------------------------------------------------------
            # V1: crée au moins 1 ligne OrderItem (estimation au kg)
            # ----------------------------------------------------------------
            try:
                from decimal import Decimal
                from django.conf import settings

                estimated_kg = (request.POST.get("estimated_kg") or "").strip()
                try:
                    qty = Decimal(estimated_kg) if estimated_kg else Decimal("5")
                except Exception:
                    qty = Decimal("5")

                price_per_kg = getattr(settings, "FAGNI_DEFAULT_PRICE_PER_KG", 1500)
                try:
                    unit_price = Decimal(str(price_per_kg))
                except Exception:
                    unit_price = Decimal("1500")

                # total ligne
                line_total = (qty * unit_price)

                # ⚠️ service: on met None si nullable; sinon on prendra un service par défaut (étape A)
                OrderItem.objects.create(
                    order=order,
                    service=None,
                    designation="Linge (estimation au kg)",
                    quantity=qty,
                    unit_price=unit_price,
                    total=line_total,
                )
            except Exception:
                # on ne bloque pas la commande si item échoue
                pass

            try:
                order.update_financials(save=True)
            except Exception:
                pass

            return redirect("orders:client_order_detail", order_id=order.id)

    return render(request, "orders/client_new_order.html", {
        "phone": phone,
        "customer": customer,
        "error": error,
        "name_init": name_init,
        "address_init": address_init,
    })


# -------------------------------------------------------------------
# Helpers: montants & timeline (client)
# -------------------------------------------------------------------

def _to_dec(v) -> Decimal:
    try:
        return Decimal(str(v or "0"))
    except Exception:
        return Decimal("0")


def _client_order_amounts(order: Order) -> dict:
    """
    Montants côté client = proxy vers le moteur canonique (_compute_order_pricing).

    Objectif: 1 seule source de vérité pour éviter toute divergence.
    """
    pricing = _compute_order_pricing(order)

    # prestation_total côté client = items_total (plus fiable que prestation_total si data legacy)
    prestation_total = pricing.get("items_total", Decimal("0"))

    return {
        "prestation_total": prestation_total,
        "service_fee": pricing.get("service_fee", Decimal("0")),
        "delivery_fee": pricing.get("delivery_fee", Decimal("0")),
        "express_extra_fee": pricing.get("express_extra_fee", Decimal("0")),
        "vat_fagni": pricing.get("vat_fagni", Decimal("0")),
        "total_ttc": pricing.get("total_client", Decimal("0")),
    }


def _client_order_timeline(order: Order) -> list:
    """
    Timeline basée sur timestamps si présents, sinon fallback sur status.
    Champs optionnels : pickup_time, wash_complete_time, delivered_time
    """
    created = getattr(order, "created_at", None)
    t_pickup = getattr(order, "pickup_time", None)
    t_wash = getattr(order, "wash_complete_time", None)
    t_deliv = getattr(order, "delivered_time", None)

    st = (getattr(order, "status", "") or "").lower()

    # fallback "done" si status final
    pickup_done = bool(t_pickup) or st in ("in_progress", "done")
    wash_done = bool(t_wash) or st in ("done",)
    deliv_done = bool(t_deliv) or st in ("done",)

    return [
        {"key": "created", "label": "Commande créée", "done": bool(created), "ts": created},
        {"key": "pickup", "label": "Collecte effectuée", "done": pickup_done, "ts": t_pickup},
        {"key": "wash", "label": "Lavage terminé", "done": wash_done, "ts": t_wash},
        {"key": "delivered", "label": "Livrée", "done": deliv_done, "ts": t_deliv},
    ]


# -------------------------------------------------------------------
# Détail client ✅ FIX: sécurisation par phone, pas customer_id
# -------------------------------------------------------------------

@client_required
def client_order_detail(request, order_id):
    """
    Détail côté client : statut + timeline + legs + paiement/montants.
    Sécurisé : seulement si la commande appartient au téléphone session.
    """
    phone = _client_phone(request)

    customer = Customer.objects.filter(phone=phone).order_by("-id").first()
    if not customer:
        return redirect("orders:client_home")

    order = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .filter(pk=order_id, customer__phone=phone)
        .first()
    )
    if not order:
        return redirect("orders:client_home")

    # legs (simple)
    legs = list(
        DeliveryLeg.objects
        .filter(order=order)
        .order_by("id")
        .values("id", "leg_type", "status", "driver_amount", "driver_id")
    )

    # montants + paiement
    amounts = _client_order_amounts(order)
    payment_status = getattr(order, "payment_status", None)

    # timeline
    timeline = _client_order_timeline(order)

    items = order.items.all().order_by("id")

    return render(request, "orders/client_order_detail.html", {
        "phone": phone,
        "customer": customer,
        "order": order,
        "legs": legs,
        "items": items,

        "amounts": amounts,
        "payment_status": payment_status,
        "timeline": timeline,
    })


# -------------------------------------------------------------------
# Items client (CRUD minimal) ✅ sécurisés par phone session
# -------------------------------------------------------------------

@require_http_methods(["GET", "POST"])
@client_required
def client_order_item_new(request, order_id):
    phone = _client_phone(request)

    order = (
        Order.objects
        .select_related("customer")
        .filter(pk=order_id, customer__phone=phone)
        .first()
    )
    if not order:
        return redirect("orders:client_home")

    error = None

    if request.method == "POST":
        from decimal import Decimal

        designation = (request.POST.get("designation") or "").strip()
        quantity_raw = (request.POST.get("quantity") or "").strip()
        unit_price_raw = (request.POST.get("unit_price") or "").strip()

        if not designation:
            error = "Merci de renseigner la désignation."
        else:
            try:
                qty = Decimal(str(quantity_raw).replace(",", "."))
            except Exception:
                qty = Decimal("0")
            try:
                up = Decimal(str(unit_price_raw).replace(",", "."))
            except Exception:
                up = Decimal("0")

            if qty <= 0:
                error = "Quantité invalide."
            elif up < 0:
                error = "Prix unitaire invalide."
            else:
                line_total = qty * up

                # service nullable => None OK
                from orders.models import OrderItem
                OrderItem.objects.create(
                    order=order,
                    service=None,
                    designation=designation,
                    quantity=qty,
                    unit_price=up,
                    total=line_total,
                )

                try:
                    order.update_financials(save=True)
                except Exception:
                    pass

                return redirect("orders:client_order_detail", order_id=order.id)

    return render(request, "orders/client_order_item_form.html", {
        "order": order,
        "mode": "new",
        "error": error,
        "designation": "",
        "quantity": "",
        "unit_price": "",
    })


@require_http_methods(["GET", "POST"])
@client_required
def client_order_item_edit(request, order_id, item_id):
    phone = _client_phone(request)

    order = (
        Order.objects
        .select_related("customer")
        .filter(pk=order_id, customer__phone=phone)
        .first()
    )
    if not order:
        return redirect("orders:client_home")

    from orders.models import OrderItem
    item = (
        OrderItem.objects
        .filter(pk=item_id, order=order)
        .first()
    )
    if not item:
        return redirect("orders:client_order_detail", order_id=order.id)

    error = None

    if request.method == "POST":
        from decimal import Decimal

        designation = (request.POST.get("designation") or "").strip()
        quantity_raw = (request.POST.get("quantity") or "").strip()
        unit_price_raw = (request.POST.get("unit_price") or "").strip()

        if not designation:
            error = "Merci de renseigner la désignation."
        else:
            try:
                qty = Decimal(str(quantity_raw).replace(",", "."))
            except Exception:
                qty = Decimal("0")
            try:
                up = Decimal(str(unit_price_raw).replace(",", "."))
            except Exception:
                up = Decimal("0")

            if qty <= 0:
                error = "Quantité invalide."
            elif up < 0:
                error = "Prix unitaire invalide."
            else:
                item.designation = designation
                item.quantity = qty
                item.unit_price = up
                item.total = qty * up
                item.save(update_fields=["designation", "quantity", "unit_price", "total"])

                try:
                    order.update_financials(save=True)
                except Exception:
                    pass

                return redirect("orders:client_order_detail", order_id=order.id)

    return render(request, "orders/client_order_item_form.html", {
        "order": order,
        "mode": "edit",
        "item": item,
        "error": error,
        "designation": item.designation or "",
        "quantity": item.quantity if item.quantity is not None else "",
        "unit_price": item.unit_price if item.unit_price is not None else "",
    })


@require_http_methods(["GET", "POST"])
@client_required
def client_order_item_delete(request, order_id, item_id):
    phone = _client_phone(request)

    order = (
        Order.objects
        .select_related("customer")
        .filter(pk=order_id, customer__phone=phone)
        .first()
    )
    if not order:
        return redirect("orders:client_home")

    from orders.models import OrderItem
    item = OrderItem.objects.filter(pk=item_id, order=order).first()
    if not item:
        return redirect("orders:client_order_detail", order_id=order.id)

    if request.method == "POST":
        item.delete()
        try:
            order.update_financials(save=True)
        except Exception:
            pass
        return redirect("orders:client_order_detail", order_id=order.id)

    return render(request, "orders/client_order_item_form.html", {
        "order": order,
        "mode": "delete",
        "item": item,
    })


# -------------------------------------------------------------------
# Lookup client (inchangé)
# -------------------------------------------------------------------

@require_GET
def client_lookup(request):
    phone = _normalize_phone(request.GET.get("phone") or "")
    if not phone:
        return JsonResponse({"ok": False, "error": "missing_phone"}, status=400)

    c = Customer.objects.filter(phone=phone).order_by("-id").first()
    if not c:
        return JsonResponse({"ok": True, "found": False})

    return JsonResponse({
        "ok": True,
        "found": True,
        "name": c.name or "",
        "address": getattr(c, "address", "") or "",
        "phone": c.phone or phone,
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
    order.update_financials(save=True)  # ✅ moteur unique compute_order_financials + sauvegarde

    from orders.models import sync_legs_status_from_order
    sync_legs_status_from_order(order, save=True)

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
    """
    Contexte canonique facturation/tickets.
    Source de vérité = _compute_order_pricing(order) (inclut Express).
    On garde compute_order_amounts(order) pour compat / infos secondaires.
    """
    cfg = get_pricing_settings()

    # Compat / autres besoins (peut servir ailleurs)
    amounts = compute_order_amounts(order)

    # Source de vérité montants (verrouillage)
    pricing = _compute_order_pricing(order)

    vat_rate = _get_vat_rate_percent(cfg)

    prestations_amount = _q(pricing.get("items_total", 0))
    delivery_amount    = _q(pricing.get("delivery_fee", 0))
    service_amount     = _q(pricing.get("service_fee", 0))
    express_amount     = _q(pricing.get("express_extra_fee", 0))
    vat_amount         = _q(pricing.get("vat_fagni", 0))

    # HT client = prestations + livraison + service + express (sans TVA)
    total_ht_client = _q(prestations_amount + delivery_amount + service_amount + express_amount)

    # TTC client = total canonique (inclut TVA)
    total_ttc_client = _q(pricing.get("total_client", 0))

    # Compat anciennes variables
    base_ht = total_ht_client
    grand_total = total_ttc_client

    # ✅ exposer "express_client" pour les templates existants
    express_client = express_amount

    # (Optionnel mais utile partout) : lignes standardisées
    invoice_lines = {
        "prestations": prestations_amount,
        "delivery": delivery_amount,
        "service": service_amount,
        "express": express_amount,
        "vat": vat_amount,
        "total_ht": total_ht_client,
        "total_ttc": total_ttc_client,
    }

    return {
        "cfg": cfg,
        "amounts": amounts,          # compat existant
        "pricing": pricing,          # debug/ops si besoin
        "invoice_lines": invoice_lines,

        # Champs canon
        "prestations_amount": prestations_amount,
        "delivery_amount": delivery_amount,
        "service_amount": service_amount,
        "express_amount": express_amount,
        "vat_rate": vat_rate,
        "vat_amount": vat_amount,
        "total_ht_client": total_ht_client,
        "total_ttc_client": total_ttc_client,

        # Compat historique
        "express_client": express_client,
        "base_ht": base_ht,
        "tva_amount": vat_amount,
        "grand_total": grand_total,
    }


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
    try:
        pdf = HTML(string=html_string, base_url=request.build_absolute_uri("/")).write_pdf()
    except Exception as e:
        return HttpResponse(
            f"<h1>Erreur génération PDF (WeasyPrint)</h1><pre>{e}</pre><hr>{html_string}",
            content_type="text/html",
            status=500,
        )

    filename = f"FACTURE-{order.invoice_number or order.code or order.id}.pdf"
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{smart_str(filename)}"'
    return response


from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse

# ... (le reste de tes imports déjà présents)

def _dec(v):
    try:
        if v in (None, "", False):
            return Decimal("0")
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def _enrich_items_for_ticket(items_qs):
    """
    Ajoute sur chaque item (sans toucher aux champs DB ni aux @property) :
      - ticket_total : total sûr (item.total si >0 sinon quantity*unit_price sinon line_total)
      - ticket_name  : designation sûre (designation > service.name > "—")
    """
    items = list(items_qs)

    for it in items:
        # --- Total robuste ---
        total_db = _dec(getattr(it, "total", None))

        if total_db > 0:
            total = total_db
        else:
            q = _dec(getattr(it, "quantity", 0))
            pu = _dec(getattr(it, "unit_price", 0))
            total_calc = q * pu

            # fallback ultime : la @property line_total (lecture seule)
            if total_calc <= 0:
                try:
                    total_calc = _dec(getattr(it, "line_total", 0))
                except Exception:
                    total_calc = Decimal("0")

            total = total_calc

        # ⚠️ Ne pas faire it.line_total = ...
        setattr(it, "ticket_total", total)

        # --- Nom robuste ---
        name = getattr(it, "designation", None)
        if not name:
            svc = getattr(it, "service", None)
            name = getattr(svc, "name", None) if svc else None

        setattr(it, "ticket_name", name or "—")

    return items


@login_required
def order_ticket_pdf(request, order_id):
    order = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .prefetch_related("items__service", "items__photos")
        .filter(pk=order_id)
        .first()
    )
    if not order:
        messages.error(request, f"Commande introuvable (ID={order_id}).")
        return redirect("orders:list")

    items = _enrich_items_for_ticket(order.items.all())

    detail_url = request.build_absolute_uri(reverse("orders:detail", args=[order.id]))
    qr_b64 = _qr_png_base64(detail_url)

    # Montants harmonisés pour template
    ctx = _build_invoice_context(order)
    amounts = ctx.get("amounts") or {}
    prestations_amount = _dec(amounts.get("subtotal", 0))
    delivery_amount = _dec(amounts.get("delivery_fee_client", 0))
    service_amount = _dec(amounts.get("service_fee_ht", 0))
    express_amount = _dec(ctx.get("express_client", 0))  # déjà normalisé
    vat_amount = _dec(ctx.get("vat_amount", 0))
    total_ttc_client = _dec(ctx.get("total_ttc_client", 0))

    context = {
        "order": order,
        "items": items,

        # QR
        "qr_data": detail_url,
        "qr_b64": qr_b64,

        # Contexte facture/pricing existant
        **ctx,
        "invoice_settings": get_invoice_settings_clean(),

        # ✅ variables harmonisées (A4 + thermique)
        "prestations_amount": prestations_amount,
        "delivery_amount": delivery_amount,
        "service_amount": service_amount,
        "express_amount": express_amount,
        "vat_amount": vat_amount,
        "total_ttc_client": total_ttc_client,
    }
    return render(request, "orders/ticket_pdf.html", context)


@login_required
def order_ticket_thermal_pdf(request, order_id):
    """
    Ticket thermique (80mm).
    Source de vérité montants = _build_invoice_context(order) (compute_order_amounts)
    Lignes = line_total calculé ici pour éviter tout écart template/model.
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

    items = _enrich_items_for_ticket(order.items.all())

    ctx = _build_invoice_context(order)
    amounts = ctx.get("amounts") or {}

    invoice_settings = get_invoice_settings_clean()
    if invoice_settings:
        ctx["invoice_settings"] = invoice_settings

    prestations_amount = _dec(amounts.get("subtotal", 0))
    delivery_amount = _dec(amounts.get("delivery_fee_client", 0))
    service_amount = _dec(amounts.get("service_fee_ht", 0))
    express_amount = _dec(ctx.get("express_client", 0))
    vat_amount = _dec(ctx.get("vat_amount", 0))
    total_ttc_client = _dec(ctx.get("total_ttc_client", 0))

    # QR total
    qr_total = total_ttc_client
    customer_phone = ""
    if getattr(order, "customer", None) and getattr(order.customer, "phone", None):
        customer_phone = order.customer.phone

    try:
        qr_total_int = int(_dec(qr_total))
    except Exception:
        qr_total_int = 0

    qr_data = f"CMD:{order.code}|TEL:{customer_phone}|TOTAL:{qr_total_int}"
    qr_b64 = _qr_png_base64(qr_data)

    context = {
        "order": order,
        "items": items,
        "qr_data": qr_data,
        "qr_b64": qr_b64,

        **ctx,

        # ✅ variables harmonisées
        "prestations_amount": prestations_amount,
        "delivery_amount": delivery_amount,
        "service_amount": service_amount,
        "express_amount": express_amount,
        "vat_amount": vat_amount,
        "total_ttc_client": total_ttc_client,
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
        margin=Sum("logistic_margin"),
    )

    total_distance = agg.get("dist") or 0
    total_logistic_margin = agg.get("margin") or 0

    # ✅ Revenu livreur NET (source de vérité) = payout in - adjustment out
    tx_qs = WalletTransaction.objects.filter(
        order__in=orders_qs,
        leg__isnull=False,
        type__in=["payout", "adjustment"],
    )

    # si filtre livreur appliqué → on restreint à son wallet
    if driver_id:
        tx_qs = tx_qs.filter(wallet__delivery_partner_id=driver_id)

    total_driver_cost = tx_qs.aggregate(net=_wallet_net_expr()).get("net") or Decimal("0")

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
    )

    # ✅ revenu livreur net basé sur WalletTransaction
    tx_qs = WalletTransaction.objects.filter(
        order__in=orders_qs,
        leg__isnull=False,
        type__in=["payout", "adjustment"],
    )

    # driver_mode => uniquement le wallet du livreur connecté
    if driver_mode and current_driver:
        tx_qs = tx_qs.filter(wallet__delivery_partner=current_driver)
    elif selected_driver_id:
        tx_qs = tx_qs.filter(wallet__delivery_partner_id=selected_driver_id)

    aggregates["total_driver_income"] = tx_qs.aggregate(net=_wallet_net_expr()).get("net") or Decimal("0")

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

    ✅ En plus :
    - Quand wash_done est validé (linge prêt), on ASSIGNE automatiquement
      le 2e tronçon (delivery/return) s'il était en pending, pour ce même livreur.
    """
    from partners.models import DeliveryPartner  # import local pour éviter les cycles

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
            return HttpResponseForbidden("Aucun profil livreur associé à cet email.")

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

        # ✅ Déclencheur : linge prêt → assigne le 2e tronçon (prestataire → client)
        # On ne touche QUE les legs pending du même livreur assigné à la commande.
        if action == "wash_done" and getattr(order, "delivery_partner_id", None):
            try:
                DeliveryLeg.objects.filter(
                    order=order,
                    driver=order.delivery_partner,
                    leg_type__in=["delivery", "return"],  # legacy support
                    status="pending",
                ).update(status="assigned")
            except Exception:
                # On ne bloque jamais la timeline sur une erreur de synchro legs
                pass

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
    month_start = timezone.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

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
    Résout le livreur "connecté" (profil DeliveryPartner) de façon robuste.

    Priorité :
    1) ?driver_id=xx (autorisé si staff OU en DEBUG pour faciliter les tests)
    2) mapping par email : request.user.email == DeliveryPartner.email
    3) fallback : order.delivery_partner (si order fourni)
    4) fallback DEV : premier livreur actif (DEBUG uniquement)
    """
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None

    is_debug = bool(getattr(settings, "DEBUG", False))
    is_staff = bool(getattr(user, "is_staff", False))

    # 1) driver_id via URL (staff OU debug)
    driver_id = (request.GET.get("driver_id") or "").strip()
    if driver_id and (is_staff or is_debug):
        d = DeliveryPartner.objects.filter(pk=driver_id, is_active=True).first()
        if d:
            return d

    # 2) mapping par email (prod)
    email = (getattr(user, "email", "") or "").strip()
    if email:
        d = DeliveryPartner.objects.filter(email__iexact=email, is_active=True).first()
        if d:
            return d

    # 3) fallback order
    if order is not None:
        dp = getattr(order, "delivery_partner", None)
        if dp and getattr(dp, "is_active", True):
            return dp

    # 4) fallback DEV
    if is_debug:
        return DeliveryPartner.objects.filter(is_active=True).order_by("id").first()

    return None


def normalize_order_legs(order, driver=None):
    """
    Verrouille la cohérence legs (idempotent) pour une commande.

    Règles :
    - 1 seul driver "actif" = order.delivery_partner (si défini)
    - Pour ce driver actif : au plus 1 pickup actif + 1 return actif
    - Les legs d'autres drivers => canceled (sauf done/canceled)
    - wash_complete_time :
        * NULL  => return actif ne peut pas être assigned/in_progress -> pending
        * OK    => return actif pending -> assigned
    - Ne touche JAMAIS aux legs done/canceled (historique)
    """

    STATUS_RANK = {"pending": 1, "assigned": 2, "in_progress": 3, "done": 4, "canceled": 0}

    def _advance_status_only(current: str, target: str) -> str:
        c = (current or "pending").lower()
        t = (target or "pending").lower()
        # canceled/done ne bougent plus
        if c in ("canceled", "done"):
            return c
        # on n’accepte que si ça avance
        return t if STATUS_RANK.get(t, 1) > STATUS_RANK.get(c, 1) else c

    from django.db import transaction

    from orders.models import DeliveryLeg

    if not order:
        return

    assigned = getattr(order, "delivery_partner", None)

    # driver cible (si driver passé, on normalise sur l'assignation quand elle existe)
    target_driver = assigned or driver

    with transaction.atomic():
        # 1) Annuler les legs "actifs" des autres drivers (si commande assignée)
        if assigned:
            other_active = (
                DeliveryLeg.objects
                .select_for_update()
                .filter(order=order)
                .exclude(driver=assigned)
                .exclude(status__in=["done", "canceled"])
            )
            other_active.update(status="canceled")

        # 2) Pour le driver cible : garder 1 pickup + 1 return actifs (le plus récent)
        if target_driver:
            for lt in ["pickup", "return"]:
                qs = (
                    DeliveryLeg.objects
                    .select_for_update()
                    .filter(order=order, driver=target_driver, leg_type=lt)
                    .exclude(status__in=["done", "canceled"])
                    .order_by("-id")
                )
                keep = qs.first()
                if keep:
                    qs.exclude(id=keep.id).update(status="canceled")

            # 3) Sync statut return selon wash_complete_time (sur le return actif)
            wash_ready = bool(getattr(order, "wash_complete_time", None))
            qs_return = (
                DeliveryLeg.objects
                .select_for_update()
                .filter(order=order, driver=target_driver, leg_type="return")
                .exclude(status__in=["done", "canceled"])
            )

            if wash_ready:
                # ✅ upgrade only
                qs_return.filter(status="pending").update(status="assigned")
            else:
                # ✅ ne JAMAIS rétrograder (sinon ça casse les tests + l’état réel côté livreur)
                # (on bloque juste l'auto-upgrade tant que wash_complete_time est NULL)
                pass


def ensure_default_driver_legs(order, driver):
    """
    SAFE helper pour la vue livreur :
    - normalise les legs (anti-doublons, 1 driver actif)
    - ne fait un sync "models" QUE si aucun leg actif n'existe (legacy)
    - si un type manque (pickup/return), crée UNIQUEMENT le leg manquant
      sans toucher aux statuts déjà en cours.
    """
    from decimal import Decimal, ROUND_HALF_UP
    from orders.models import DeliveryLeg

    if not order or not driver:
        return DeliveryLeg.objects.none()

    # 🔒 0) Normalisation globale (évite doublons et multi-drivers actifs)
    try:
        normalize_order_legs(order, driver=driver)
    except Exception:
        pass

    # 🔒 Garde-fou : si commande assignée, on ne gère que le driver assigné
    assigned = getattr(order, "delivery_partner", None)
    if assigned and str(getattr(driver, "id", "")) != str(getattr(assigned, "id", "")):
        return DeliveryLeg.objects.filter(order=order, driver=driver).exclude(status="canceled").order_by("id")

    # Legs actuels (actifs)
    qs = DeliveryLeg.objects.filter(order=order, driver=driver).exclude(status="canceled").order_by("id")

    # ✅ Legacy : si aucun leg actif du tout, on peut resync via models
    if not qs.exists():
        try:
            from orders.models import sync_delivery_legs_for_order
            sync_delivery_legs_for_order(order)
        except Exception:
            pass
        qs = DeliveryLeg.objects.filter(order=order, driver=driver).exclude(status="canceled").order_by("id")

    # Helper arrondi FCFA
    def _round_fcfa(v):
        if v is None:
            return Decimal("0")
        if not isinstance(v, Decimal):
            v = Decimal(str(v))
        return v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

    # Si un type manque, on crée uniquement le leg manquant (sans resync global)
    have_pickup = qs.filter(leg_type="pickup").exists()
    have_return = qs.filter(leg_type="return").exists()

    # Base status
    if getattr(order, "status", None) == "done":
        base_pickup = "done"
        base_return = "done"
    elif getattr(order, "status", None) == "in_progress":
        base_pickup = "assigned"
        # return ne peut être assigned que si linge prêt
        base_return = "assigned" if getattr(order, "wash_complete_time", None) else "pending"
    else:
        base_pickup = "pending"
        base_return = "pending"

    # Données financières (split 50/50) — fallback 0
    delivery_fee = Decimal(str(getattr(order, "delivery_fee", 0) or 0))
    driver_total = Decimal(str(getattr(order, "amount_driver_partner", 0) or 0))
    margin_total = Decimal(str(getattr(order, "logistic_margin", 0) or 0))

    client_share_1 = _round_fcfa(delivery_fee / 2)
    client_share_2 = _round_fcfa(delivery_fee - client_share_1)

    driver_share_1 = _round_fcfa(driver_total / 2)
    driver_share_2 = _round_fcfa(driver_total - driver_share_1)

    margin_share_1 = _round_fcfa(margin_total / 2)
    margin_share_2 = _round_fcfa(margin_total - margin_share_1)

    # Distance (si connue)
    distance_total = getattr(order, "distance_km", None) or getattr(order, "distance_km_total", None) or 0
    try:
        distance_total = Decimal(str(distance_total or 0))
    except Exception:
        distance_total = Decimal("0")
    distance_one_way = None
    if distance_total > 0:
        distance_one_way = (distance_total / Decimal("2")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Création du leg manquant seulement
    try:
        if not have_pickup:
            DeliveryLeg.objects.create(
                order=order,
                driver=driver,
                leg_type="pickup",
                status=base_pickup,
                distance_km=float(distance_one_way) if distance_one_way is not None else None,
                client_fee_share=client_share_1,
                driver_amount=driver_share_1,
                fagni_margin=margin_share_1,
            )

        if not have_return:
            DeliveryLeg.objects.create(
                order=order,
                driver=driver,
                leg_type="return",
                status=base_return,
                distance_km=float(distance_one_way) if distance_one_way is not None else None,
                client_fee_share=client_share_2,
                driver_amount=driver_share_2,
                fagni_margin=margin_share_2,
            )
    except Exception:
        pass

    # Re-load + ajuste return selon wash_complete_time (sans toucher done/canceled)
    qs = DeliveryLeg.objects.filter(order=order, driver=driver).exclude(status="canceled").order_by("id")

    try:
        wash_ready = bool(getattr(order, "wash_complete_time", None))
        r = qs.filter(leg_type="return").exclude(status__in=["done", "canceled"]).order_by("-id").first()
        if r:
            if wash_ready and r.status == "pending":
                DeliveryLeg.objects.filter(pk=r.pk).update(status="assigned")
            elif (not wash_ready) and r.status in {"assigned", "in_progress"}:
                DeliveryLeg.objects.filter(pk=r.pk).update(status="pending")
        qs = DeliveryLeg.objects.filter(order=order, driver=driver).exclude(status="canceled").order_by("id")
    except Exception:
        pass

    return qs


@login_required
def driver_order_detail(request, order_id):
    """
    Vue détail course côté LIVREUR.

    - Montre uniquement les infos utiles au chauffeur
    - Calcule la distance & le montant qui lui reviennent à partir des DeliveryLeg
    - Crée des DeliveryLeg par défaut s'il n'y en a pas encore
    - Affiche des montants cohérents (moteur compute_totals + fallbacks)
    """

    from decimal import Decimal
    from django.db.models import Sum
    from django.shortcuts import get_object_or_404, redirect, render
    from django.urls import reverse

    order = get_object_or_404(
        Order.objects.select_related("customer", "delivery_partner", "laundry_partner"),
        pk=order_id,
    )

    # ------------------------------------------------------------
    # Driver à utiliser pour la vue "détail course"
    # Règle :
    # - côté livreur (non-staff) : accès UNIQUEMENT si driver_id == order.delivery_partner_id
    # - staff : peut consulter; si order.delivery_partner est vide, peut fallback via ?driver_id
    # ------------------------------------------------------------
    selected_driver_id = (request.GET.get("driver_id") or "").strip()

    assigned_driver = getattr(order, "delivery_partner", None)  # peut être None (legacy)
    driver = assigned_driver  # par défaut on se cale sur l'assignation

    if not request.user.is_staff:
        # Sans assignation : on refuse (sinon on ne sait pas "qui" a le droit)
        if not assigned_driver:
            return render(
                request,
                "orders/driver_wallet.html",
                {"error_message": "Commande non assignée à un livreur : accès refusé côté livreur."},
            )

        # driver_id obligatoire et doit matcher l'assignation
        if not selected_driver_id:
            return render(
                request,
                "orders/driver_wallet.html",
                {"error_message": "Livreur non identifié (driver_id manquant). Ouvre l’app livreur puis clique sur la course."},
            )

        if str(assigned_driver.id) != str(selected_driver_id):
            return render(
                request,
                "orders/driver_wallet.html",
                {"error_message": "Accès refusé : cette commande n’est pas assignée à ton profil livreur."},
            )

    else:
        # Staff : si la commande n'a pas de livreur assigné, on peut fallback sur ?driver_id (optionnel)
        if driver is None and selected_driver_id:
            driver = DeliveryPartner.objects.filter(pk=selected_driver_id).first()

    # -----------------------------
    # Defaults
    # -----------------------------
    driver_legs_qs = DeliveryLeg.objects.none()
    driver_leg_distance = Decimal("0")
    driver_leg_amount_done = Decimal("0")   # ✅ payé = legs done uniquement
    driver_leg_amount_all = Decimal("0")    # (optionnel) potentiel = tous les legs
    driver_mission_type_label = "Mission unique (A/R ou globale)"
    driver_wallet = None
    driver_wallet_url = ""

    if driver is not None:

        # ✅ SAFE : ne jamais resync pendant une course si des legs existent déjà
        # (sinon ça peut réécrire assigned/in_progress -> pending)
        try:
            if not DeliveryLeg.objects.filter(order=order).exclude(status="canceled").exists():
                from orders.models import sync_delivery_legs_for_order
                sync_delivery_legs_for_order(order)
        except Exception:
            pass

        # 🔒 Normalisation légère (anti-doublons / return pending si wash pas prêt)
        try:
            normalize_order_legs(order, driver=driver)
        except Exception:
            pass

        # 1) On charge les legs (pour le driver de la vue)
        driver_legs_qs = DeliveryLeg.objects.filter(order=order, driver=driver).exclude(status="canceled").order_by("id")

        # 2) Agrégats distance / montant (propre)
        #    - distance : somme de toutes les jambes (info logistique)
        #    - montant payé : uniquement les jambes DONE (logique métier Cas B)
        legs_agg_all = driver_legs_qs.aggregate(
            total_distance_km=Sum("distance_km"),
            total_amount=Sum("driver_amount"),
        )
        driver_leg_distance = legs_agg_all["total_distance_km"] or Decimal("0")
        driver_leg_amount_all = legs_agg_all["total_amount"] or Decimal("0")

        legs_agg_done = driver_legs_qs.filter(status="done").aggregate(
            total_amount=Sum("driver_amount"),
        )
        driver_leg_amount_done = legs_agg_done["total_amount"] or Decimal("0")

        # 3) Fallbacks legacy (très anciennes commandes sans legs)
        if (driver_leg_distance == 0) and getattr(order, "distance_km_total", None):
            try:
                driver_leg_distance = Decimal(str(order.distance_km_total))
            except Exception:
                driver_leg_distance = Decimal("0")

        # Si aucune jambe done, on ne force pas le montant "payé".
        # (On peut garder le potentiel via driver_leg_amount_all)
        # MAIS si l'ordre est DONE et qu'il n'y a pas de legs done (legacy), on fallback.
        if (driver_leg_amount_done == 0) and (getattr(order, "status", "") == "done"):
            if getattr(order, "amount_driver_partner", None):
                try:
                    driver_leg_amount_done = Decimal(str(order.amount_driver_partner))
                except Exception:
                    driver_leg_amount_done = Decimal("0")

        # 4) Libellé type de mission à partir des leg_type
        leg_types = list(driver_legs_qs.values_list("leg_type", flat=True).distinct())
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

            # Force driver_id dans le querystring
            qd = request.GET.copy()
            qd["driver_id"] = str(driver.id)
            qs = qd.urlencode()

            driver_wallet_url = reverse("wallets:driver_wallet_dashboard")
            if qs:
                driver_wallet_url = f"{driver_wallet_url}?{qs}"

        except Exception:
            driver_wallet = None
            driver_wallet_url = ""

    # ------------------------------------------------------------
    # Montants FAGNI cohérents (moteur compute_totals + fallbacks)
    # ------------------------------------------------------------
    try:
        amounts = order.compute_totals(save=False) or {}
    except Exception:
        amounts = {}

    # Total client TTC : moteur central -> champs DB -> fallback calculé
    total_client_ttc = (
        amounts.get("total_ttc_client")
        or amounts.get("total_client_ttc")
        or getattr(order, "total_client_ttc", None)
    )

    try:
        total_client_ttc = Decimal(str(total_client_ttc)) if total_client_ttc is not None else Decimal("0")
    except Exception:
        total_client_ttc = Decimal("0")

    if total_client_ttc <= 0:
        try:
            # ✅ même logique que driver_app_data : prestation_total sinon somme(items)
            from django.db.models import F as _F, Value as _Value, DecimalField as _DecimalField, Sum as _Sum
            from django.db.models.functions import Coalesce as _Coalesce, Cast as _Cast

            DEC = _DecimalField(max_digits=12, decimal_places=2)

            items_total = order.items.aggregate(
                s=_Coalesce(
                    _Sum(_Cast(_F("quantity"), DEC) * _Cast(_F("unit_price"), DEC)),
                    _Value(0, output_field=DEC),
                )
            ).get("s") or Decimal("0")

            prestation_raw = getattr(order, "prestation_total", None)
            prestation = Decimal(str(prestation_raw)) if prestation_raw not in (None, "", 0) else Decimal(str(items_total))

            if prestation <= 0:
                # fallback ultime (legacy)
                prestation = Decimal(str(getattr(order, "total", None) or 0))

            service = Decimal(str(getattr(order, "service_fee", None) or 0))
            delivery = Decimal(str(getattr(order, "delivery_fee", None) or 0))
            express = Decimal(str(getattr(order, "express_extra_fee", None) or 0))
            vat = Decimal(str(getattr(order, "vat_fagni", None) or 0))

            total_client_ttc = prestation + service + delivery + express + vat
        except Exception:
            total_client_ttc = Decimal("0")


    delivery_fee_client = (
        amounts.get("delivery_fee_client")
        or getattr(order, "delivery_fee", None)
        or Decimal("0")
    )
    try:
        delivery_fee_client = Decimal(str(delivery_fee_client))
    except Exception:
        delivery_fee_client = Decimal("0")

    service_fee_ht = amounts.get("service_fee_ht") or getattr(order, "service_fee", None) or Decimal("0")
    vat_fagni = amounts.get("vat_fagni") or getattr(order, "vat_fagni", None) or Decimal("0")
    express_surcharge = amounts.get("express_surcharge") or getattr(order, "express_extra_fee", None) or Decimal("0")

    try:
        service_fee_ht = Decimal(str(service_fee_ht))
    except Exception:
        service_fee_ht = Decimal("0")
    try:
        vat_fagni = Decimal(str(vat_fagni))
    except Exception:
        vat_fagni = Decimal("0")
    try:
        express_surcharge = Decimal(str(express_surcharge))
    except Exception:
        express_surcharge = Decimal("0")

    # Revenu livreur affiché :
    # ✅ source-of-truth = net wallet (payout/adjustment) sur cette commande
    # (aligné avec driver_app_data)
    # ✅ Revenu livreur affiché : net des tx payout/adjustment liées aux legs du driver
    driver_income = Decimal("0")
    if driver is not None:
        try:
            # ✅ anti-wallet parasite : uniquement les tx du wallet de CE driver
            qs = WalletTransaction.objects.filter(
                order=order,
                type__in=["payout", "adjustment"],
                wallet__delivery_partner=driver,
            )

            # priorité: tx liées à une leg du driver (logique par tronçon)
            net = qs.filter(leg__isnull=False, leg__driver=driver).aggregate(net=_wallet_net_expr()).get("net")

            # fallback: tx order-level sur le wallet du driver (ex: corrections/legacy)
            if net is None:
                net = qs.aggregate(net=_wallet_net_expr()).get("net")

            driver_income = net or Decimal("0")
        except Exception:
            driver_income = Decimal("0")

    else:
        # legacy : pas de driver résolu
        driver_income = (
            amounts.get("amount_driver_partner")
            or getattr(order, "amount_driver_partner_resolved", None)
            or getattr(order, "amount_driver_partner", None)
            or getattr(order, "driver_logistic_cost", None)
            or Decimal("0")
        )
        try:
            driver_income = Decimal(str(driver_income))
        except Exception:
            driver_income = Decimal("0")

    # ------------------------------------------------------------
    # KPI "reste à gagner" + % (potentiel - payé wallet)
    # ------------------------------------------------------------
    driver_income_remaining = Decimal("0")
    driver_income_progress_pct = 0

    try:
        if driver_leg_amount_all and driver_leg_amount_all > 0:
            driver_income_remaining = (driver_leg_amount_all - driver_income) if driver_leg_amount_all > driver_income else Decimal("0")
            driver_income_progress_pct = int((driver_income / driver_leg_amount_all) * 100) if driver_leg_amount_all else 0
            driver_income_progress_pct = max(0, min(100, driver_income_progress_pct))
    except Exception:
        driver_income_remaining = Decimal("0")
        driver_income_progress_pct = 0


    # ------------------------------------------------------------
    # Coordonnées (maps)
    # ------------------------------------------------------------
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

        # ✅ affichage métier
        "driver_leg_amount": driver_leg_amount_done,   # ce que la page utilisait déjà
        "driver_leg_amount_done": driver_leg_amount_done,
        "driver_leg_amount_all": driver_leg_amount_all,  # potentiel (si tu veux l’afficher dans le template)

        "driver_mission_type_label": driver_mission_type_label,
        "driver_wallet": driver_wallet,
        "driver_wallet_url": driver_wallet_url,

        "amounts": amounts,
        "total_client_ttc": total_client_ttc,
        "delivery_fee_client": delivery_fee_client,
        "driver_income": driver_income,
        "driver_income_remaining": driver_income_remaining,
        "driver_income_progress_pct": driver_income_progress_pct,

        "pickup_coords": pickup_coords,
        "delivery_coords": delivery_coords,
        "provider_coords": provider_coords,
        "pickup_lat": pickup_lat,
        "pickup_lng": pickup_lng,
        "delivery_lat": delivery_lat,
        "delivery_lng": delivery_lng,
        "provider_lat": provider_lat,
        "provider_lng": provider_lng,

        "service_fee_ht": service_fee_ht,
        "vat_fagni": vat_fagni,
        "express_surcharge": express_surcharge,

    }

    return render(request, "orders/driver_order_detail.html", context)


# ===============================================
#  APP LIVREUR – PAGE
# ===============================================
@login_required
def driver_app(request):
    """
    Page App livreur : UI shell + driver connecté.

    IMPORTANT:
    - Les listes + KPIs sont alimentés par /orders/driver-app/data/ (source-of-truth),
      afin d'éviter les incohérences entre order.status et legs.status.
    - Livreur non-staff: voit uniquement ses courses (filtrage appliqué dans driver_app_data)
    - Staff: peut filtrer via ?driver_id=7 (filtrage appliqué dans driver_app_data)
    """
    from django.shortcuts import render

    connected_driver = _get_connected_driver(request)
    selected_driver_id = (request.GET.get("driver_id") or "").strip()

    context = {
        "connected_driver": connected_driver,
        "selected_driver_id": selected_driver_id,
    }
    return render(request, "orders/driver_app.html", context)


# ===============================================
#  APP LIVREUR – DATA JSON pour refresh KPIs
# ===============================================
# ===============================================
@login_required
def driver_app_data(request):
    """
    Version JSON live de l'app livreur :
    - KPIs (counts, distance, income)
    - listes de commandes (pending/in_progress/done) pour refresh UI
    IMPORTANT:
      - filtrage aligné avec driver_app()
      - grouping basé sur les DeliveryLeg (source de vérité UI), pas sur order.status

    INCOME:
      - paid = WalletTransaction net (payout/adjustment)
      - potential = somme des montants potentiels des legs (fallback)
      - display = paid si >0 sinon potential
    """
    from decimal import Decimal
    from django.http import JsonResponse
    from django.db.models import Sum, F, Value, DecimalField, Case, When
    from django.db.models.functions import Coalesce, Cast
    from django.utils.timezone import localtime

    user = request.user
    connected_driver = _get_connected_driver(request)
    selected_driver_id = (request.GET.get("driver_id") or "").strip()

    # ==========================
    # BASE QUERY (ALIGNÉE driver_app)
    # ==========================
    qs = Order.objects.select_related("customer", "laundry_partner", "delivery_partner")

    if not user.is_staff:
        if connected_driver:
            qs = qs.filter(delivery_partner=connected_driver)
        else:
            qs = qs.none()
    else:
        if selected_driver_id:
            qs = qs.filter(delivery_partner_id=selected_driver_id)
        else:
            qs = qs  # staff voit tout par défaut

    qs = qs.order_by("-created_at")

    # ==========================
    # ANNOTATIONS (montants + distance)
    # ==========================
    DEC = DecimalField(max_digits=12, decimal_places=2)

    items_total_expr = Coalesce(
        Sum(Cast(F("items__quantity"), DEC) * Cast(F("items__unit_price"), DEC)),
        Value(0, output_field=DEC),
    )

    prestation_expr = Coalesce(F("prestation_total"), items_total_expr, output_field=DEC)
    service_expr = Coalesce(F("service_fee"), Value(0, output_field=DEC))
    delivery_expr = Coalesce(F("delivery_fee"), Value(0, output_field=DEC))
    vat_expr = Coalesce(F("vat_fagni"), Value(0, output_field=DEC))
    express_expr = Coalesce(F("express_extra_fee"), Value(0, output_field=DEC))

    total_client_fallback_expr = prestation_expr + service_expr + delivery_expr + express_expr + vat_expr

    # ✅ IMPORTANT: si total_client_ttc == 0 => fallback (pas Coalesce)
    total_client_expr = Case(
        When(total_client_ttc__gt=0, then=F("total_client_ttc")),
        default=total_client_fallback_expr,
        output_field=DEC,
    )

    distance_expr = Coalesce(F("distance_km_total"), Value(0, output_field=DEC), output_field=DEC)

    qs = qs.annotate(
        items_total=items_total_expr,
        total_client_display=total_client_expr,
        driver_distance_display=distance_expr,
    )

    # IMPORTANT: on récupère les legs pour calculer un statut UI fiable + potentiel revenu
    qs = qs.prefetch_related("legs")

    # ==========================================================
    # ✅ INCOME SOURCE-OF-TRUTH: WalletTransaction net par commande
    #    priorité: tx liées à un leg
    #    fallback: tx order-level (leg NULL) sur wallet driver
    # ==========================================================
    order_ids = list(qs.values_list("id", flat=True))

    base_tx = WalletTransaction.objects.filter(
        order_id__in=order_ids,
        type__in=["payout", "adjustment"],
        wallet__owner_type="driver",                 # ✅ IMPORTANT: uniquement wallets livreurs
    )
    base_tx = base_tx.filter(wallet__delivery_partner__isnull=False)

    driver_target_id = None
    if not user.is_staff and connected_driver:
        driver_target_id = connected_driver.id
    elif user.is_staff and selected_driver_id:
        try:
            driver_target_id = int(selected_driver_id)
        except Exception:
            driver_target_id = None

    if driver_target_id:
        base_tx = base_tx.filter(wallet__delivery_partner_id=driver_target_id)

    # 1) priorité: legs-level
    income_rows_legs = (
        base_tx.filter(leg__isnull=False)
        .values("order_id")
        .annotate(net=_wallet_net_expr())
    )
    income_by_order_id = {r["order_id"]: (r["net"] or Decimal("0")) for r in income_rows_legs}

    # 2) fallback: order-level (leg NULL) uniquement si pas déjà présent
    missing_ids = [oid for oid in order_ids if oid not in income_by_order_id]
    if missing_ids:
        income_rows_order = (
            base_tx.filter(order_id__in=missing_ids, leg__isnull=True)
            .values("order_id")
            .annotate(net=_wallet_net_expr())
        )
        for r in income_rows_order:
            income_by_order_id[r["order_id"]] = (r["net"] or Decimal("0"))

    def _safe_float(x):
        try:
            return float(x or 0)
        except Exception:
            return 0.0

    def compute_status_from_legs(order):
        """
        Source de vérité UI:
          - si un leg in_progress OU assigned => in_progress
          - sinon si un leg pending => pending
          - sinon si tous done => done
          - sinon fallback: order.status
        """
        legs = list(order.legs.all())
        if not legs:
            return order.status

        st = [str(l.status or "").lower() for l in legs]

        # ✅ assigned = course démarrée côté UI
        if any(s in ("in_progress", "assigned") for s in st):
            return "in_progress"

        if any(s == "pending" for s in st):
            return "pending"

        # si tous les legs actifs sont done (on tolère canceled)
        active = [s for s in st if s != "canceled"]
        if active and all(s == "done" for s in active):
            return "done"

        return order.status

    def _dec0(x):
        try:
            return Decimal(str(x or "0"))
        except Exception:
            return Decimal("0")

    def _leg_driver_potential(leg) -> Decimal:
        """
        Retourne la part livreur potentielle d'un leg.
        On essaie plusieurs noms de champs possibles pour rester robuste.
        """
        for attr in (
            "amount_driver",
            "driver_amount",
            "driver_share",
            "driver_fee",
            "driver_income",
            "amount_driver_partner",
            "amount_driver_potential",
            "driver_logistic_cost",
        ):
            if hasattr(leg, attr):
                v = getattr(leg, attr, None)
                d = _dec0(v)
                if d > 0:
                    return d
        return Decimal("0")

    def _order_driver_potential_from_legs(order) -> Decimal:
        try:
            legs = list(order.legs.all())
        except Exception:
            legs = []
        total = Decimal("0")
        for leg in legs:
            total += _leg_driver_potential(leg)
        return total

    def serialize(order, computed_status):
        customer = getattr(order, "customer", None)

        total_client_num = _safe_float(getattr(order, "total_client_display", 0) or 0)

        # ✅ montant déjà payé (DB)
        paid_num = _safe_float(getattr(order, "amount_paid", 0) or 0)

        # ✅ fallback robustesse: si payment_status dit "paid" mais amount_paid=0
        payment_status = str(getattr(order, "payment_status", "") or "").lower().strip()
        if total_client_num > 0 and paid_num <= 0 and payment_status in ("paid", "completed", "succeeded"):
            paid_num = total_client_num

        # ✅ dû = max(0, total - payé)
        due_num = total_client_num - paid_num
        if due_num < 0:
            due_num = 0.0

        # ✅ Label paiement
        if total_client_num <= 0:
            pay_label = "À CALCULER"
            total_client_out = None   # JS affiche "—"
            due_out = 0.0
        else:
            total_client_out = total_client_num
            due_out = due_num

            if due_num <= 0:
                pay_label = "PAYÉ"
            elif 0 < due_num < total_client_num:
                pay_label = "PARTIEL"
            else:
                pay_label = "À ENCAISSER"

        # si c'est "à calculer", on masque le revenu potentiel (cohérence UI)
        income_out = 0.0
        income_paid = Decimal("0")
        income_potential = Decimal("0")

        # ✅ revenu livreur: payé si dispo, sinon potentiel
        income_paid = income_by_order_id.get(order.id, Decimal("0")) or Decimal("0")
        income_potential = _order_driver_potential_from_legs(order)

        # ✅ revenu livreur affiché: projection = max(paid, potential)
        # - paid = net wallet déjà crédité
        # - potential = somme estimée (legs)
        # => on affiche la projection la plus haute (cohérence KPI + UI)
        # ✅ afficher TOUJOURS le payé s’il existe, même si < potentiel
        if income_paid > 0:
            income_out = _safe_float(income_paid)
        else:
            income_out = _safe_float(income_potential)

        # ✅ distance
        dist_out = _safe_float(getattr(order, "driver_distance_display", 0) or 0)

        detail_url = f"/orders/driver/orders/{order.id}/"
        dp_id = getattr(order, "delivery_partner_id", None)
        if dp_id:
            detail_url = f"{detail_url}?driver_id={dp_id}"

        return {
            "id": order.id,
            "code": order.code,
            "status": computed_status,
            "raw_status": order.status,

            "pay_label": pay_label,
            "created_at": localtime(order.created_at).strftime("%d/%m/%Y %H:%M") if order.created_at else None,
            "customer_name": getattr(customer, "name", None) or "Client",
            "customer_phone": getattr(customer, "phone", "") if customer else "",
            "customer_address": getattr(customer, "address", "") if customer else "",

            "total_client": total_client_out,
            "due_amount": due_out,

            "driver_income": income_out,
            "driver_distance": dist_out,

            "detail_url": detail_url,

            "driver_income_paid": _safe_float(income_paid),
            "driver_income_potential": _safe_float(income_potential),
    }

    # ==========================
    # BUILD DATA (grouping by computed_status)
    # ==========================
    orders_all = list(qs[:300])

    orders_pending_list = []
    orders_in_progress_list = []
    orders_done_list = []

    pending_count = 0
    in_progress_count = 0
    done_count = 0

    # KPI totals
    total_distance = Decimal("0")
    total_income_paid = Decimal("0")
    total_income_potential = Decimal("0")
    total_income_display = Decimal("0")  # ✅ cohérent avec serialize(): paid si dispo sinon potential

    for o in orders_all:
        cs = compute_status_from_legs(o)

        total_distance += Decimal(str(_safe_float(getattr(o, "driver_distance_display", 0) or 0)))

        paid = income_by_order_id.get(o.id, Decimal("0")) or Decimal("0")
        total_income_paid += paid

        pot = _order_driver_potential_from_legs(o)
        total_income_potential += pot

        # ✅ règle identique à serialize(): projection = max(paid, potential)
        total_income_display += max(paid, pot)

        if cs == "pending":
            pending_count += 1
            if len(orders_pending_list) < 60:
                orders_pending_list.append(serialize(o, cs))
        elif cs == "in_progress":
            in_progress_count += 1
            if len(orders_in_progress_list) < 60:
                orders_in_progress_list.append(serialize(o, cs))
        elif cs == "done":
            done_count += 1
            if len(orders_done_list) < 60:
                orders_done_list.append(serialize(o, cs))

    return JsonResponse({
        "pending": pending_count,
        "in_progress": in_progress_count,
        "done": done_count,

        "total_distance_km": float(total_distance),

        # NEW: paid/potential/display
        "total_driver_income_paid": float(total_income_paid),
        "total_driver_income_potential": float(total_income_potential),
        "total_driver_income_display": float(total_income_display),

        # keep backward compatibility (old key)
        "total_driver_income": float(total_income_paid),

        "source_distance": "distance_km_total",
        "source_income": "total_driver_income_display=sum(max(paid_wallet, potential_legs)) • cards=paid_else_potential",
        "server_time": localtime().strftime("%H:%M:%S"),

        "orders_pending": orders_pending_list,
        "orders_in_progress": orders_in_progress_list,
        "orders_done": orders_done_list,
    })


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
        Sum(Cast(F("items__quantity"), DEC) * Cast(F("items__unit_price"), DEC)),
        Value(0, output_field=DEC),
    )

    # ✅ Prestation = prestation_total si dispo, sinon somme des items
    prestation_expr = Coalesce(F("prestation_total"), items_total_expr, output_field=DEC)

    service_expr = Coalesce(F("service_fee"), Value(0, output_field=DEC))
    delivery_expr = Coalesce(F("delivery_fee"), Value(0, output_field=DEC))
    express_expr = Coalesce(F("express_extra_fee"), Value(0, output_field=DEC))
    vat_expr = Coalesce(F("vat_fagni"), Value(0, output_field=DEC))

    total_client_fallback_expr = prestation_expr + service_expr + delivery_expr + express_expr + vat_expr

    # ✅ IMPORTANT : si total_client_ttc == 0 => fallback (pas Coalesce)
    total_client_expr = Case(
        When(total_client_ttc__gt=0, then=Cast(F("total_client_ttc"), DEC)),
        default=total_client_fallback_expr,
        output_field=DEC,
    )

    income_expr = Coalesce(
        F("amount_driver_partner"),
        Coalesce(F("driver_logistic_cost"), Value(0, output_field=DEC)),
        output_field=DEC,
    )

    distance_expr = Coalesce(F("distance_km_total"), Value(0, output_field=DEC), output_field=DEC)

    qs = qs.annotate(
        items_total=items_total_expr,
        total_client_display=total_client_expr,
        driver_income_display=income_expr,
        driver_distance_display=distance_expr,
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

    def _has_coords(obj) -> bool:
        if not obj:
            return False
        lat = getattr(obj, "latitude", None)
        lng = getattr(obj, "longitude", None)
        return bool(lat and lng)

    def _is_calc_order(order) -> bool:
        customer = getattr(order, "customer", None)
        laundry = getattr(order, "laundry_partner", None)
        has_laundry = bool(getattr(order, "laundry_partner_id", None))
        customer_ok = _has_coords(customer)
        laundry_ok = _has_coords(laundry)

        try:
            dist_total_num = float(getattr(order, "driver_distance_display", 0) or 0)
        except Exception:
            dist_total_num = 0.0

        return (not has_laundry) or (not customer_ok) or (not laundry_ok) or (dist_total_num <= 0)

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

        calc = _is_calc_order(order)

        dist_val = float(getattr(order, "driver_distance_display", 0) or 0) if not calc else 0.0
        total_val = float(order.total_client_display or 0) if not calc else 0.0
        income_val = float(order.driver_income_display or 0) if not calc else 0.0

        writer.writerow([
            order.code or order.id,
            order.created_at.strftime("%Y-%m-%d %H:%M") if order.created_at else "",
            getattr(customer, "name", "") if customer else "",
            getattr(customer, "phone", "") if customer else "",
            getattr(customer, "address", "") if customer else "",
            order.get_status_display(),
            dist_val,
            total_val,
            income_val,
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
    - garde-fou “À CALCULER” :
        * si commande non calculable => distance = 0, total = 0, revenu = 0
    - mise en forme Excel : en-têtes stylées, auto-filter, freeze pane, totaux.
    """
    from io import BytesIO
    from datetime import datetime

    from django.http import HttpResponse
    from django.db.models import Sum, Value, F, Case, When
    from django.db.models.functions import Coalesce, Cast
    from django.db.models import DecimalField

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

    user = request.user
    connected_driver = _get_connected_driver(request)
    driver_id = None

    # --- Identification du livreur (livreur connecté ou driver_id en GET pour un staff) ---
    if connected_driver:
        driver_id = connected_driver.id
    elif user.is_staff:
        driver_id_param = (request.GET.get("driver_id") or "").strip()
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
    items_total_expr = Coalesce(
        Sum(
            Cast(F("items__quantity"), DEC) * Cast(F("items__unit_price"), DEC)
        ),
        Value(0, output_field=DEC),
    )

    prestation_expr = Coalesce(F("prestation_total"), items_total_expr, output_field=DEC)
    service_expr = Coalesce(F("service_fee"), Value(0, output_field=DEC))
    delivery_expr = Coalesce(F("delivery_fee"), Value(0, output_field=DEC))
    vat_expr = Coalesce(F("vat_fagni"), Value(0, output_field=DEC))


    express_expr = Coalesce(F("express_extra_fee"), Value(0, output_field=DEC))
    base_ht_expr = prestation_expr + service_expr + delivery_expr
    total_client_fallback = base_ht_expr + express_expr + vat_expr

    driver_income_expr = Coalesce(
        F("amount_driver_partner"),
        Coalesce(F("driver_logistic_cost"), Value(0, output_field=DEC)),
        output_field=DEC,
    )

    distance_expr = Coalesce(
        F("distance_km_total"),
        Value(0, output_field=DEC),
        output_field=DEC,
    )

    qs = qs.annotate(
        items_total=items_total_expr,
        total_client_display=Case(
            When(total_client_ttc__gt=0, then=Cast(F("total_client_ttc"), DEC)),
            default=total_client_fallback,
            output_field=DEC,
        ),
        driver_income_display=driver_income_expr,
        driver_distance_display=distance_expr,
    )

    # ---------- GARDE-FOU “À CALCULER” ----------
    def _has_coords(obj) -> bool:
        if not obj:
            return False
        lat = getattr(obj, "latitude", None)
        lng = getattr(obj, "longitude", None)
        return bool(lat and lng)

    def _is_calc_order(order) -> bool:
        customer = getattr(order, "customer", None)
        laundry = getattr(order, "laundry_partner", None)

        has_laundry = bool(getattr(order, "laundry_partner_id", None))
        customer_ok = _has_coords(customer)
        laundry_ok = _has_coords(laundry)

        try:
            dist_total_num = float(getattr(order, "distance_km_total", 0) or 0)
        except Exception:
            dist_total_num = 0.0

        return (not has_laundry) or (not customer_ok) or (not laundry_ok) or (dist_total_num <= 0)

    # ---------- CRÉATION DU FICHIER EXCEL ----------
    wb = Workbook()
    ws = wb.active
    ws.title = "Courses livreur"

    # Styles de base
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2563EB")  # bleu
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    money_format = "#,##0"
    distance_format = "0.00"

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
        "Paiement",  # (optionnel mais utile)
    ]

    ws.append(headers)

    # Header style
    for col_idx, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    # Freeze + autofilter
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"

    # Lignes
    row_index = 1
    for order in qs:
        row_index += 1
        customer = getattr(order, "customer", None)

        calc = _is_calc_order(order)

        code = order.code or str(order.id)
        created = order.created_at.strftime("%d/%m/%Y %H:%M") if order.created_at else ""
        client_name = getattr(customer, "name", "") if customer else ""
        phone = getattr(customer, "phone", "") if customer else ""
        address = getattr(customer, "address", "") if customer else ""
        status_display = order.get_status_display()

        # ✅ alignement “À CALCULER”
        if calc:
            distance_km = 0.0
            total_client = 0.0
            driver_income = 0.0
            pay_label = "À CALCULER"
        else:
            distance_km = float(getattr(order, "driver_distance_display", 0) or 0)
            total_client = float(getattr(order, "total_client_display", 0) or 0)
            driver_income = float(getattr(order, "driver_income_display", 0) or 0)

            # label paiement similaire à l'API (simple)
            paid_num = 0.0
            try:
                paid_num = float(getattr(order, "amount_paid", 0) or 0)
            except Exception:
                paid_num = 0.0

            due_num = total_client - paid_num
            if due_num < 0:
                due_num = 0.0

            if total_client <= 0:
                pay_label = "À CALCULER"
            elif due_num <= 0:
                pay_label = "PAYÉ"
            elif 0 < due_num < total_client:
                pay_label = "PARTIEL"
            else:
                pay_label = "À ENCAISSER"

        ws.append([
            code,
            created,
            client_name,
            phone,
            address,
            status_display,
            distance_km,
            total_client,
            driver_income,
            pay_label,
        ])

        # Bordures + formats
        for col_idx in range(1, len(headers) + 1):
            cell = ws.cell(row=row_index, column=col_idx)
            cell.border = thin_border
            cell.alignment = Alignment(vertical="top", wrap_text=True)

            # Distance (km)
            if col_idx == 7:
                cell.number_format = distance_format

            # Montants (FCFA)
            if col_idx in (8, 9):
                cell.number_format = money_format

    # Totaux (ligne de synthèse)
    total_row = row_index + 1
    ws.cell(row=total_row, column=1, value="TOTAUX").font = Font(bold=True)
    ws.cell(row=total_row, column=1).border = thin_border

    # Sommes sur distance / total client / revenu
    ws.cell(row=total_row, column=7, value=f"=SUM(G2:G{row_index})").number_format = distance_format
    ws.cell(row=total_row, column=8, value=f"=SUM(H2:H{row_index})").number_format = money_format
    ws.cell(row=total_row, column=9, value=f"=SUM(I2:I{row_index})").number_format = money_format

    for col_idx in (7, 8, 9):
        c = ws.cell(row=total_row, column=col_idx)
        c.font = Font(bold=True)
        c.border = thin_border

    # Largeurs de colonnes (premium lisible)
    widths = {
        1: 16,  # code
        2: 18,  # date
        3: 22,  # client
        4: 16,  # phone
        5: 38,  # address
        6: 14,  # statut
        7: 14,  # distance
        8: 20,  # total client
        9: 20,  # revenu livreur
        10: 14, # paiement
    }
    for col_idx, w in widths.items():
        ws.column_dimensions[get_column_letter(col_idx)].width = w

    # ---------- RÉPONSE HTTP ----------
    now = datetime.now().strftime("%Y%m%d_%H%M")
    filename_parts = ["driver_app", "export", now]
    if driver_id:
        filename_parts.insert(1, f"driver_{driver_id}")
    filename = "_".join(filename_parts) + ".xlsx"

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
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
    # --- Agrégats simples (sans distance) ---
    raw_stats = qs.aggregate(
        total_orders=Count("id", distinct=True),
        # adapte ce filtre si tu as déjà un start_month dans ta fonction
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
        "month_orders": month_qs.count(),
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
    Hub livreur :
    - affiche KPIs + listes (pending / in_progress / done)
    - alimente le template orders/driver_hub.html
    """
    connected_driver = _get_connected_driver(request)

    # Valeurs par défaut (hub vide si pas de driver)
    context = {
        "connected_driver": connected_driver,
        "pending_orders": [],
        "in_progress_orders": [],
        "done_orders": [],
        "kpi_today_count": 0,
        "kpi_today_income": Decimal("0"),
        "kpi_week_done_count": 0,
        "kpi_week_income": Decimal("0"),
        "kpi_month_done_count": 0,
        "kpi_month_income": Decimal("0"),
    }

    if not connected_driver:
        return render(request, "orders/driver_hub.html", context)

    # Base queryset
    qs = (
        Order.objects
        .filter(delivery_partner=connected_driver)
        .select_related("customer", "laundry_partner", "delivery_partner")
        .prefetch_related("items__service")
        .order_by("-created_at")
    )

    # -----------------------------
    # KPIs
    # -----------------------------
    today = timezone.localdate()
    today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
    today_end = timezone.make_aware(datetime.combine(today, datetime.max.time()))

    today_qs = qs.filter(created_at__gte=today_start, created_at__lte=today_end)

    kpi_today_count = today_qs.count()
    kpi_today_income = (
        today_qs.aggregate(s=Coalesce(Sum("driver_logistic_cost"), Decimal("0")))["s"]
        or Decimal("0")
    )

    week_start = timezone.now() - timedelta(days=7)
    week_qs = qs.filter(created_at__gte=week_start)

    kpi_week_done_count = week_qs.filter(status="done").count()
    kpi_week_income = (
        week_qs.aggregate(s=Coalesce(Sum("driver_logistic_cost"), Decimal("0")))["s"]
        or Decimal("0")
    )

    month_start = timezone.now() - timedelta(days=30)
    month_qs = qs.filter(created_at__gte=month_start)

    kpi_month_done_count = month_qs.filter(status="done").count()
    kpi_month_income = (
        month_qs.aggregate(s=Coalesce(Sum("driver_logistic_cost"), Decimal("0")))["s"]
        or Decimal("0")
    )

    # -----------------------------
    # Listes affichées dans le hub
    # -----------------------------
    pending_orders = list(qs.filter(status="pending")[:20])
    in_progress_orders = list(qs.filter(status="in_progress")[:20])
    done_orders = list(qs.filter(status="done")[:20])

    # -----------------------------
    # Enrichissement affichage montants (pour le template)
    # total_client_display / driver_income_display
    # -----------------------------
    def enrich(order):
        try:
            amounts = compute_order_amounts(order) or {}
        except Exception:
            amounts = {}

        # Montant client (fallback safe)
        total_client = (
            amounts.get("total_client_ttc")
            or amounts.get("total_client")
            or amounts.get("total_ttc_client")
            or amounts.get("grand_total")
            or order.total
            or Decimal("0")
        )

        # Revenu livreur (fallback: driver_logistic_cost)
        driver_income = (
            amounts.get("driver_income")
            or amounts.get("driver_cost")
            or order.driver_logistic_cost
            or Decimal("0")
        )

        # Injecte des "attributs" que le template utilise
        order.total_client_display = total_client
        order.driver_income_display = driver_income
        return order

    pending_orders = [enrich(o) for o in pending_orders]
    in_progress_orders = [enrich(o) for o in in_progress_orders]
    done_orders = [enrich(o) for o in done_orders]

    context.update({
        "pending_orders": pending_orders,
        "in_progress_orders": in_progress_orders,
        "done_orders": done_orders,
        "kpi_today_count": kpi_today_count,
        "kpi_today_income": kpi_today_income,
        "kpi_week_done_count": kpi_week_done_count,
        "kpi_week_income": kpi_week_income,
        "kpi_month_done_count": kpi_month_done_count,
        "kpi_month_income": kpi_month_income,
    })

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
    dt_start = None

    if period == "today":
        dt_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "7d":
        dt_start = timezone.now() - timedelta(days=7)
    elif period == "30d":
        dt_start = timezone.now() - timedelta(days=30)
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
        tx_qs = WalletTransaction.objects.filter(
            order=order,
            leg__isnull=False,
            type__in=["payout", "adjustment"],
        )
        if driver_id:
            tx_qs = tx_qs.filter(wallet__delivery_partner_id=driver_id)

        driver_income = tx_qs.aggregate(net=_wallet_net_expr()).get("net") or 0

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

        def _d(v):
            try:
                return Decimal(str(v)) if v is not None else Decimal("0.00")
            except Exception:
                return Decimal("0.00")

        tc = _d(data.get("total_client_ttc")) or _d(getattr(o, "total_client_ttc", None))

        # ✅ IMPORTANT : si tc == 0 => fallback calculé (PAS order.total)
        if tc <= 0:
            items_total = _d(data.get("prestation_total")) or _d(getattr(o, "prestation_total", None))

            service_fee = _d(data.get("service_fee_ht")) or _d(getattr(o, "service_fee", None))
            delivery_fee = _d(data.get("delivery_fee_client")) or _d(getattr(o, "delivery_fee", None))
            vat_fagni = _d(data.get("vat_fagni")) or _d(getattr(o, "vat_fagni", None))
            express_for_client = _d(data.get("express_for_client"))  # si tu veux l’inclure dans tc fallback

            tc = items_total + service_fee + delivery_fee + express_for_client + vat_fagni


        total_client = tc
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
def driver_missions_history(request):
    """
    V2.2 — Historique des missions (lecture seule) + filtres + export (CSV/XLSX)

    Source de vérité :
      - Potentiel = somme des driver_amount sur les legs du driver (par commande)
      - Payé (net) = WalletTransaction (payout/adjustment) du wallet driver, par commande
      - Reste = max(potentiel - payé, 0)

    Filtres (GET) :
      - status: all|pending|in_progress|done
      - q: recherche (code, nom client, téléphone, adresse)
      - from/to: plage dates (created_at)
      - min_remaining=1 : affiche seulement reste > 0
      - sort: newest|oldest|remaining_desc|paid_desc
      - export: csv|xlsx (mêmes filtres, sans pagination)
    """
    from decimal import Decimal
    from urllib.parse import urlencode
    import csv
    from datetime import datetime

    from django.core.paginator import Paginator
    from django.db.models import Sum, Q
    from django.http import (
        HttpResponse,
        HttpResponseForbidden,
    )
    from django.utils.timezone import localtime, make_aware

    # openpyxl (déjà utilisé ailleurs chez toi)
    try:
        from openpyxl import Workbook
    except Exception:
        Workbook = None

    user = request.user
    connected_driver = _get_connected_driver(request)
    selected_driver_id = (request.GET.get("driver_id") or "").strip()

    # -----------------------------
    # 0) Lire filtres GET
    # -----------------------------
    status_filter = (request.GET.get("status") or "all").strip()
    q = (request.GET.get("q") or "").strip()
    date_from = (request.GET.get("from") or "").strip()
    date_to = (request.GET.get("to") or "").strip()
    sort = (request.GET.get("sort") or "newest").strip()
    min_remaining = (request.GET.get("min_remaining") or "").strip() in ("1", "true", "on", "yes")
    export = (request.GET.get("export") or "").strip().lower()

    # -----------------------------
    # 1) Déterminer le driver cible
    # -----------------------------
    driver = None
    if user.is_staff or user.is_superuser:
        if selected_driver_id:
            driver = DeliveryPartner.objects.filter(pk=selected_driver_id).first()
        if not driver and connected_driver:
            driver = connected_driver
    else:
        # non-staff => uniquement le livreur connecté
        driver = connected_driver
        if not driver:
            return HttpResponseForbidden("Accès refusé : aucun profil livreur connecté.")

    # -----------------------------
    # Helper parse date (input type=date => YYYY-MM-DD)
    # -----------------------------
    def _parse_date_ymd(s: str):
        if not s:
            return None
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    d_from = _parse_date_ymd(date_from)
    d_to = _parse_date_ymd(date_to)

    # -----------------------------
    # 2) Orders concernés = orders où ce driver a au moins 1 leg
    # -----------------------------
    order_ids = (
        DeliveryLeg.objects
        .filter(driver=driver)
        .values_list("order_id", flat=True)
        .distinct()
    )

    qs = (
        Order.objects
        .filter(id__in=order_ids)
        .select_related("customer", "laundry_partner", "delivery_partner")
        .prefetch_related("legs")
        .order_by("-created_at")
    )

    # Filtre dates (created_at)
    # On filtre au niveau datetime pour rester robuste
    if d_from:
        # début de journée
        dt = datetime(d_from.year, d_from.month, d_from.day, 0, 0, 0)
        try:
            dt = make_aware(dt)
        except Exception:
            pass
        qs = qs.filter(created_at__gte=dt)

    if d_to:
        # fin de journée
        dt = datetime(d_to.year, d_to.month, d_to.day, 23, 59, 59)
        try:
            dt = make_aware(dt)
        except Exception:
            pass
        qs = qs.filter(created_at__lte=dt)

    # Recherche DB (réduit la charge)
    if q:
        qs = qs.filter(
            Q(code__icontains=q)
            | Q(customer__name__icontains=q)
            | Q(customer__phone__icontains=q)
            | Q(customer__address__icontains=q)
        )

    # -----------------------------
    # 3) Potentiel par commande (somme driver_amount sur legs du driver)
    # -----------------------------
    potential_rows = (
        DeliveryLeg.objects
        .filter(order_id__in=order_ids, driver=driver)
        .values("order_id")
        .annotate(potential=Sum("driver_amount"))
    )
    potential_by_order = {r["order_id"]: (r["potential"] or 0) for r in potential_rows}

    # -----------------------------
    # 4) Payé net par commande (wallet tx)
    #    payout + adjustment, leg NULL ou pas (legacy inclus)
    # -----------------------------
    tx_qs = WalletTransaction.objects.filter(
        order_id__in=order_ids,
        type__in=["payout", "adjustment"],
        wallet__delivery_partner=driver,
    )

    income_rows = (
        tx_qs.values("order_id")
        .annotate(net=_wallet_net_expr())
    )
    paid_by_order = {r["order_id"]: (r["net"] or Decimal("0")) for r in income_rows}

    # -----------------------------
    # 5) Construire items (UI-ready)
    # -----------------------------
    def fmt_dt(dt):
        if not dt:
            return ""
        try:
            return localtime(dt).strftime("%d/%m/%Y %H:%M")
        except Exception:
            return str(dt)

    items = []
    for o in qs:
        legs_for_driver = [
            l for l in getattr(o, "legs", []).all()
            if str(getattr(l, "driver_id", "")) == str(driver.id)
        ]

        statuses = [getattr(l, "status", None) for l in legs_for_driver]

        # statut mission (UI) basé sur legs
        if any(s == "in_progress" for s in statuses):
            mission_status = "in_progress"
        elif any(s in ("pending", "assigned") for s in statuses):
            mission_status = "pending"
        elif statuses and all(s in ("done", "canceled") for s in statuses):
            mission_status = "done"
        else:
            mission_status = (getattr(o, "status", None) or "pending")

        potential = Decimal(str(potential_by_order.get(o.id, 0) or 0))
        paid = Decimal(str(paid_by_order.get(o.id, Decimal("0")) or 0))
        remaining = potential - paid
        if remaining < 0:
            remaining = Decimal("0")

        # total km (legs)
        total_km = Decimal("0")
        for l in legs_for_driver:
            try:
                total_km += Decimal(str(getattr(l, "distance_km", 0) or 0))
            except Exception:
                pass

        items.append({
            "order": o,
            "code": getattr(o, "code", "") or f"#{o.id}",
            "created_at": fmt_dt(getattr(o, "created_at", None)),
            "created_at_raw": getattr(o, "created_at", None),
            "mission_status": mission_status,
            "legs": [{
                "id": l.id,
                "leg_type": getattr(l, "leg_type", ""),
                "status": getattr(l, "status", ""),
                "distance_km": float(getattr(l, "distance_km", 0) or 0),
                "driver_amount": float(getattr(l, "driver_amount", 0) or 0),
                "started_at": fmt_dt(getattr(l, "started_at", None)),
                "finished_at": fmt_dt(getattr(l, "finished_at", None)),
            } for l in legs_for_driver],
            "potential": float(potential),
            "paid": float(paid),
            "remaining": float(remaining),
            "total_km": float(total_km),
            "customer_name": getattr(getattr(o, "customer", None), "name", "") or "",
            "customer_phone": getattr(getattr(o, "customer", None), "phone", "") or "",
            "customer_address": getattr(getattr(o, "customer", None), "address", "") or "",
            "laundry_name": getattr(getattr(o, "laundry_partner", None), "name", "") or "",
            "detail_url": f"/orders/driver/orders/{o.id}/",
        })

    # 6) Filtres Python (mission_status + remaining)
    if status_filter and status_filter != "all":
        if status_filter == "canceled":
            items = [it for it in items if it.get("mission_status") in ("canceled", "cancelled")]
        else:
            items = [it for it in items if it.get("mission_status") == status_filter]

    if min_remaining:
        items = [it for it in items if (it.get("remaining") or 0) > 0]

    # -----------------------------
    # 7) Tri
    # -----------------------------
    if sort == "oldest":
        items.sort(key=lambda x: (x.get("created_at_raw") is None, x.get("created_at_raw")))
    elif sort == "remaining_desc":
        items.sort(key=lambda x: (x.get("remaining") or 0), reverse=True)
    elif sort == "paid_desc":
        items.sort(key=lambda x: (x.get("paid") or 0), reverse=True)
    else:
        # newest
        items.sort(key=lambda x: (x.get("created_at_raw") is None, x.get("created_at_raw")), reverse=True)


    # -----------------------------
    # 8) Export CSV/XLSX (sans pagination)
    # -----------------------------
    def _export_filename(ext: str) -> str:
        base = f"missions_driver_{driver.id}"
        return f"{base}.{ext}"

    def _status_label(s: str) -> str:
        m = {
            "pending": "En attente",
            "assigned": "En attente",
            "in_progress": "En cours",
            "done": "Terminée",
            "canceled": "Annulée",
            "cancelled": "Annulée",
        }
        return m.get((s or "").strip(), (s or "").strip())

    if export == "csv":
        resp = HttpResponse(content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{_export_filename("csv")}"'
        w = csv.writer(resp)
        w.writerow([
            "Date", "Code", "Statut",
            "Client", "Téléphone", "Adresse",
            "Blanchisserie",
            "Potentiel", "Payé", "Reste",
            "Km (total)", "Nb legs",
            "Legs (détails)",
            "Lien détail",
        ])
        for it in items:
            legs_txt = " | ".join(
                f"{(l.get('leg_type') or '')}:{(l.get('status') or '')}:{int(float(l.get('driver_amount') or 0))}FCFA:{float(l.get('distance_km') or 0):.2f}km"
                for l in (it.get("legs") or [])
            )
            w.writerow([
                it.get("created_at") or "",
                it.get("code") or "",
                _status_label(it.get("mission_status") or ""),
                it.get("customer_name") or "",
                it.get("customer_phone") or "",
                it.get("customer_address") or "",
                it.get("laundry_name") or "",
                int(float(it.get("potential") or 0)),
                int(float(it.get("paid") or 0)),
                int(float(it.get("remaining") or 0)),
                float(it.get("total_km") or 0),
                len(it.get("legs") or []),
                legs_txt,
                it.get("detail_url") or "",
            ])
        return resp

    if export == "xlsx":
        if Workbook is None:
            return HttpResponse("Export XLSX indisponible (openpyxl manquant).", status=500)

        wb = Workbook()
        ws = wb.active
        ws.title = "Missions"

        ws.append([
            "Date", "Code", "Statut",
            "Client", "Téléphone", "Adresse",
            "Blanchisserie",
            "Potentiel", "Payé", "Reste",
            "Km (total)", "Nb legs",
            "Legs (détails)",
            "Lien détail",
        ])

        for it in items:
            legs_txt = " | ".join(
                f"{(l.get('leg_type') or '')}:{(l.get('status') or '')}:{int(float(l.get('driver_amount') or 0))}FCFA:{float(l.get('distance_km') or 0):.2f}km"
                for l in (it.get("legs") or [])
            )
            ws.append([
                it.get("created_at") or "",
                it.get("code") or "",
                _status_label(it.get("mission_status") or ""),
                it.get("customer_name") or "",
                it.get("customer_phone") or "",
                it.get("customer_address") or "",
                it.get("laundry_name") or "",
                int(float(it.get("potential") or 0)),
                int(float(it.get("paid") or 0)),
                int(float(it.get("remaining") or 0)),
                float(it.get("total_km") or 0),
                len(it.get("legs") or []),
                legs_txt,
                it.get("detail_url") or "",
            ])

        resp = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        resp["Content-Disposition"] = f'attachment; filename="{_export_filename("xlsx")}"'
        wb.save(resp)
        return resp

    # -----------------------------
    # 9) KPI (sur items filtrés)
    # -----------------------------
    kpi_total = len(items)
    kpi_potential = sum(float(it.get("potential") or 0) for it in items)
    kpi_paid = sum(float(it.get("paid") or 0) for it in items)
    kpi_remaining = sum(float(it.get("remaining") or 0) for it in items)
    kpi_km = sum(float(it.get("total_km") or 0) for it in items)

    # -----------------------------
    # 10) Pagination
    # -----------------------------
    paginator = Paginator(items, 12)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    # conserver les params (sans page/export) pour pagination & links
    preserved = {}
    for k, v in request.GET.items():
        if k in ("page", "export"):
            continue
        if v is None:
            continue
        preserved[k] = v
    qs_params = urlencode(preserved)

    context = {
        "connected_driver": connected_driver,
        "driver": driver,
        "selected_driver_id": selected_driver_id,
        "page_obj": page_obj,
        "qs_params": qs_params,
        # pour pré-remplir le form
        "status_filter": status_filter,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "sort": sort,
        "min_remaining": min_remaining,

        # KPI
        "kpi_total": kpi_total,
        "kpi_potential": kpi_potential,
        "kpi_paid": kpi_paid,
        "kpi_remaining": kpi_remaining,
        "kpi_km": kpi_km,
    }
    return render(request, "orders/driver_missions_history.html", context)


@login_required
def driver_missions_export_csv(request):
    params = request.GET.copy()
    params["export"] = "csv"
    return redirect(f"/orders/driver/missions/?{params.urlencode()}")

@login_required
def driver_missions_export_xlsx(request):
    params = request.GET.copy()
    params["export"] = "xlsx"
    return redirect(f"/orders/driver/missions/?{params.urlencode()}")


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
    Option B — 2 legs dès le départ (niveau commande) :

    - Si des legs existent déjà -> on ne recrée rien (idempotent)
    - Sinon, on crée :
        * pickup (assigned)
        * return (pending si wash pas prêt, sinon assigned)
    - Distance et montant répartis entre les jambes.
    - Garde-fou : pas de legs si commande non chiffrée (items + total_client_ttc > 0)
    """
    from decimal import Decimal, InvalidOperation

    existing_legs = DeliveryLeg.objects.filter(order=order)

    if existing_legs.exists():
        wash_ready = bool(getattr(order, "wash_complete_time", None))

        qs_return = existing_legs.filter(leg_type="return").exclude(status__in=["done", "canceled"])

        if wash_ready:
            # pending -> assigned
            qs_return.filter(status="pending").update(status="assigned")
        else:
            # assigned/in_progress -> pending (linge pas prêt)
            qs_return.filter(status__in=["assigned", "in_progress"]).update(status="pending")

        return existing_legs

    driver = getattr(order, "delivery_partner", None)
    if not driver:
        return existing_legs

    # ----------------------------
    # Garde-fou : commande chiffrée
    # ----------------------------
    has_items = False
    try:
        has_items = order.items.exists()
    except Exception:
        has_items = False

    total_client = getattr(order, "total_client_ttc", None)
    if total_client is None:
        total_client = getattr(order, "total", None)

    try:
        from orders.models import DeliveryLeg, sync_delivery_legs_for_order
        if not DeliveryLeg.objects.filter(order=order).exclude(status="canceled").exists():
            sync_delivery_legs_for_order(order)
    except Exception:
        pass

    if (not has_items) or total_client_dec <= 0:
        return existing_legs

    # ----------------------------
    # Distances
    # ----------------------------
    total_distance = (
        getattr(order, "distance_km_total", None)
        or getattr(order, "distance_km", None)
        or 0
    )
    pickup_distance = getattr(order, "distance_km_pickup", None)
    delivery_distance = getattr(order, "distance_km_delivery", None)

    if pickup_distance is not None and delivery_distance is not None:
        pass
    elif pickup_distance is not None and delivery_distance is None:
        if total_distance and pickup_distance <= total_distance:
            delivery_distance = max(total_distance - pickup_distance, 0)
        else:
            delivery_distance = pickup_distance
    elif delivery_distance is not None and pickup_distance is None:
        if total_distance and delivery_distance <= total_distance:
            pickup_distance = max(total_distance - delivery_distance, 0)
        else:
            pickup_distance = delivery_distance
    else:
        if total_distance:
            pickup_distance = total_distance / 2
            delivery_distance = total_distance / 2
        else:
            pickup_distance = 0
            delivery_distance = 0

    try:
        pickup_distance_dec = Decimal(str(pickup_distance or 0))
        delivery_distance_dec = Decimal(str(delivery_distance or 0))
    except (InvalidOperation, TypeError, ValueError):
        pickup_distance_dec = Decimal("0")
        delivery_distance_dec = Decimal("0")

    # ----------------------------
    # Montant livreur total
    # ----------------------------
    total_amount = getattr(order, "amount_driver_partner", None)
    if not total_amount:
        total_amount = (
            getattr(order, "driver_logistic_cost", None)
            or getattr(order, "delivery_fee", None)
            or getattr(order, "delivery_cost_driver", None)
            or 0
        )

    if (not total_amount or float(total_amount) <= 0) and hasattr(order, "compute_totals"):
        try:
            totals = order.compute_totals(save=False) or {}
            total_amount = (
                totals.get("amount_driver_partner")
                or totals.get("driver_amount")
                or totals.get("driver_logistic_cost")
                or totals.get("delivery_cost_driver")   # ✅ TON CAS
                or 0
            )
        except Exception:
            total_amount = 0

    try:
        total_amount_dec = Decimal(str(total_amount or 0))
    except (InvalidOperation, TypeError, ValueError):
        total_amount_dec = Decimal("0")

    # Répartition montant
    dist_sum = pickup_distance_dec + delivery_distance_dec
    if dist_sum > 0 and total_amount_dec > 0:
        ratio_pickup = pickup_distance_dec / dist_sum
        pickup_amount_dec = (total_amount_dec * ratio_pickup)
        pickup_amount = int(pickup_amount_dec.quantize(Decimal("1")))
        delivery_amount = int(total_amount_dec) - pickup_amount
    else:
        pickup_amount = int(total_amount_dec / 2) if total_amount_dec > 0 else 0
        delivery_amount = int(total_amount_dec) - pickup_amount if total_amount_dec > 0 else 0

    # ✅ Source unique : création/normalisation legs dans models (anti-doublons)
    try:
        from orders.models import sync_delivery_legs_for_order
        sync_delivery_legs_for_order(order)
    except Exception:
        pass

    return (
        DeliveryLeg.objects
        .filter(order=order, driver=driver)
        .exclude(status="canceled")
        .order_by("id")
    )


def update_leg_status(leg, action, user=None):
    """
    Met à jour proprement le statut d'une DeliveryLeg selon l'action demandée.

    Actions autorisées :
    - accept  : pending → assigned
    - start   : assigned / pending → in_progress
    - finish  : assigned / in_progress → done
    - cancel  : * → canceled (sauf done)

    Retourne (changed: bool, message: str)
    """
    from django.utils import timezone

    old_status = leg.status
    # ✅ Auto-fix: si le leg n'a pas de driver, on prend le driver assigné à la commande
    try:
        if getattr(leg, "driver_id", None) is None:
            assigned = getattr(getattr(leg, "order", None), "delivery_partner", None)
            if assigned:
                leg.driver = assigned
                leg.save(update_fields=["driver"])
    except Exception:
        pass

    action = (action or "").lower().strip()

    # 🔒 Garde-fou métier : le "return/delivery" ne démarre pas tant que le linge n'est pas prêt
    if getattr(leg, "leg_type", None) == "return":
        if getattr(leg, "status", None) not in {"done", "canceled"}:
            if not getattr(getattr(leg, "order", None), "wash_complete_time", None):
                if action in {"accept", "start", "finish"}:
                    return False, "Retour impossible : linge pas prêt (wash_complete_time manquant)."

    # 🔒 Normalisation anti-doublons avant transition
    try:
        normalize_order_legs(getattr(leg, "order", None), driver=getattr(leg, "driver", None))
    except Exception:
        pass

    if action == "accept":
        if leg.status == "pending":
            leg.status = "assigned"

    elif action == "start":
        if leg.status in {"pending", "assigned"}:
            leg.status = "in_progress"
            if not getattr(leg, "started_at", None):
                leg.started_at = timezone.now()

    elif action == "finish":
        if leg.status in {"assigned", "in_progress"}:
            leg.status = "done"
            if not getattr(leg, "finished_at", None):
                leg.finished_at = timezone.now()

    elif action == "cancel":
        # ✅ Autoriser annulation même si done, mais uniquement staff (utile pour tests / admin)
        if leg.status == "done":
            if not user or not getattr(user, "is_staff", False):
                return False, "Annulation impossible : leg déjà terminé (réservé au staff)."

        if leg.status != "canceled":
            leg.status = "canceled"

            # ✅ reverse payout s'il a déjà été payé
            try:
                from decimal import Decimal
                from django.db.models import Sum
                from wallets.models import WalletTransaction
                from wallets.services import debit_wallet, get_or_create_wallet_for_delivery_partner

                wallet = get_or_create_wallet_for_delivery_partner(leg.driver)

                paid = WalletTransaction.objects.filter(
                    leg=leg, type="payout", direction="in"
                ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

                reversed_amt = WalletTransaction.objects.filter(
                    leg=leg, type="adjustment", direction="out"
                ).aggregate(s=Sum("amount"))["s"] or Decimal("0")

                to_reverse = paid - reversed_amt
                if to_reverse > 0:
                    debit_wallet(
                        wallet,
                        to_reverse,
                        label=f"Annulation leg #{leg.id} – reverse payout",
                        order=leg.order,
                        leg=leg,  # ✅ important
                        tx_type="adjustment",
                    )
            except Exception:
                pass

    else:
        return False, "Action inconnue"

    if leg.status == old_status:
        return False, f"Aucune transition valide depuis le statut '{old_status}'"

    update_fields = ["status"]
    if hasattr(leg, "started_at"):
        update_fields.append("started_at")
    if hasattr(leg, "finished_at"):
        update_fields.append("finished_at")

    leg.save(update_fields=update_fields)

    # ✅ Auto-sync après transition
    try:
        order = getattr(leg, "order", None)
        if order:
            from orders.models import sync_order_status_from_legs
            sync_order_status_from_legs(order, save=True)

            # 1) Si return terminé, s'assurer que wash_complete_time existe
            if getattr(leg, "leg_type", None) == "return" and leg.status == "done":
                if not getattr(order, "wash_complete_time", None):
                    order.wash_complete_time = leg.finished_at or timezone.now()
                    order.save(update_fields=["wash_complete_time"])

            # 2) Payout + reconcile UNIQUEMENT si commande payée (évite paiements fantômes)
            try:
                if leg.status == "done":
                    if getattr(order, "payment_status", "unpaid") == "paid":
                        _trigger_driver_payout_for_leg(leg)

                        # ✅ RECONCILE: si payouts existants != driver_amount => adjustment correctif
                        from decimal import Decimal
                        from django.db.models import Sum
                        from wallets.models import WalletTransaction
                        from wallets.services import (
                            credit_wallet,
                            debit_wallet,
                            get_or_create_wallet_for_delivery_partner,
                        )

                        target = Decimal(str(leg.driver_amount or 0))

                        paid_in = (
                            WalletTransaction.objects.filter(leg=leg, type="payout", direction="in")
                            .aggregate(s=Sum("amount"))["s"]
                            or Decimal("0")
                        )
                        adj_in = (
                            WalletTransaction.objects.filter(leg=leg, type="adjustment", direction="in")
                            .aggregate(s=Sum("amount"))["s"]
                            or Decimal("0")
                        )
                        adj_out = (
                            WalletTransaction.objects.filter(leg=leg, type="adjustment", direction="out")
                            .aggregate(s=Sum("amount"))["s"]
                            or Decimal("0")
                        )

                        net = Decimal(str(paid_in)) + Decimal(str(adj_in)) - Decimal(str(adj_out))
                        diff = target - net

                        if diff != 0:
                            wallet = get_or_create_wallet_for_delivery_partner(leg.driver)
                            if diff > 0:
                                credit_wallet(
                                    wallet,
                                    diff,
                                    label=f"Correction payout leg #{leg.id} (diff +)",
                                    order=order,
                                    leg=leg,
                                    tx_type="adjustment",
                                )
                            else:
                                debit_wallet(
                                    wallet,
                                    -diff,
                                    label=f"Correction payout leg #{leg.id} (diff -)",
                                    order=order,
                                    leg=leg,
                                    tx_type="adjustment",
                                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).exception(
                    "Driver payout failed for leg #%s: %s",
                    getattr(leg, "id", None),
                    e,
                )

    except Exception:
        pass

    # 🔒 Normalisation après transition (ex: cancel d’un leg ancien)
    try:
        normalize_order_legs(getattr(leg, "order", None), driver=getattr(leg, "driver", None))
    except Exception:
        pass

    return True, f"Statut mis à jour : {old_status} → {leg.status}"


from orders.service_layer.payouts import trigger_driver_payout_for_leg

def _trigger_driver_payout_for_leg(leg):
    return trigger_driver_payout_for_leg(leg)


def _redirect_back(request, fallback_name, **kwargs):
    """
    Utilise ?back=… si présent et sûr, sinon redirige vers un named URL (fallback).
    """
    back = (request.GET.get("back") or request.POST.get("back") or "").strip()
    if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        return redirect(back)
    return redirect(fallback_name, **kwargs)


@require_POST
@login_required
def driver_leg_action(request, leg_id, action):
    leg = get_object_or_404(
        DeliveryLeg.objects.select_related("order", "driver"),
        pk=leg_id
    )
    order = leg.order
    driver = leg.driver

    # 🔒 Permission : soit staff, soit le livreur assigné connecté (via DeliveryPartner.user)
    if not request.user.is_staff:
        dp_user = getattr(driver, "user", None)
        if dp_user is not None and dp_user != request.user:
            messages.error(request, "Accès refusé.")
            return _redirect_back(request, "orders:driver_app")

    # 🔐 Actions autorisées (garde-fou param)
    action = (action or "").lower().strip()
    if action not in {"accept", "start", "finish", "cancel"}:
        messages.error(request, "Action inconnue.")
        return _redirect_back(request, "orders:driver_order_detail", order_id=order.id)

    # ⚙️ Transition + auto-payout (gérée dans update_leg_status)
    changed, msg = update_leg_status(leg=leg, action=action, user=request.user)
    from django.contrib import messages
    if changed:
        messages.success(request, msg)
    else:
        messages.info(request, msg)

    # ↩️ Redirection : priorité à ?back=…, sinon détail de la course
    return _redirect_back(request, "orders:driver_order_detail", order_id=order.id)


@login_required
def driver_order_live_status(request, order_id):
    """
    Lot 4.4 — Endpoint JSON pour rafraîchir la vue livreur sans reload.

    Règles:
    - staff/superuser: peut demander ?driver_id=... sinon fallback sur order.delivery_partner
    - non-staff: doit correspondre au driver assigné à la commande
      et (si DeliveryPartner.user existe) l'user doit matcher
    - payload legs: uniquement ceux du driver cible
    """
    from decimal import Decimal
    from django.db.models import Sum
    from django.utils import timezone

    order = get_object_or_404(
        Order.objects.select_related("laundry_partner", "customer", "delivery_partner"),
        pk=order_id,
    )

    def dt_iso(v):
        return v.isoformat() if v else None

    def dt_fr(v):
        if not v:
            return None
        try:
            v = timezone.localtime(v)
        except Exception:
            pass
        return v.strftime("%d/%m/%Y %H:%M")

    # ---------------------------------------
    # 1) Déterminer le driver cible
    # ---------------------------------------
    driver_id = (request.GET.get("driver_id") or "").strip()
    driver = None

    if request.user.is_staff or request.user.is_superuser:
        if driver_id:
            driver = DeliveryPartner.objects.filter(pk=driver_id).first()
        if not driver:
            driver = getattr(order, "delivery_partner", None)
    else:
        driver = getattr(order, "delivery_partner", None)
        if not driver:
            return HttpResponseForbidden("Commande non assignée à un livreur.")

        if driver_id and str(driver.id) != str(driver_id):
            return HttpResponseForbidden("Accès refusé : driver_id invalide.")

        dp_user = getattr(driver, "user", None)
        if dp_user is not None and dp_user != request.user:
            return HttpResponseForbidden("Accès refusé.")

    # ---------------------------------------
    # 2) Legs : UNIQUEMENT ceux du driver cible
    # ---------------------------------------
    if driver:
        legs_qs = (
            DeliveryLeg.objects
            .filter(order=order, driver=driver)
            .select_related("driver")
            .order_by("id")
        )
    else:
        legs_qs = DeliveryLeg.objects.none()

    legs = []
    for leg in legs_qs:
        legs.append({
            "id": leg.id,
            "leg_type": leg.leg_type,
            "status": leg.status,
            "distance_km": float(leg.distance_km or 0),
            "driver_amount": float(getattr(leg, "driver_amount", 0) or 0),
            "started_at": dt_iso(getattr(leg, "started_at", None)),
            "finished_at": dt_iso(getattr(leg, "finished_at", None)),
        })

    # ---------------------------------------
    # 3) KPI driver (wallet net + potentiel legs)
    #    ⚠️ UI: jamais négatif côté livreur
    # ---------------------------------------
    driver_leg_amount_all = Decimal("0")
    driver_leg_amount_done = Decimal("0")
    driver_income = Decimal("0")
    driver_income_remaining = Decimal("0")
    driver_income_progress_pct = 0

    try:
        if driver:
            agg_all = legs_qs.aggregate(total=Sum("driver_amount"))
            driver_leg_amount_all = Decimal(str(agg_all["total"] or 0))

            agg_done = legs_qs.filter(status="done").aggregate(total=Sum("driver_amount"))
            driver_leg_amount_done = Decimal(str(agg_done["total"] or 0))

            qs_tx = WalletTransaction.objects.filter(
                order=order,
                type__in=["payout", "adjustment"],
                wallet__delivery_partner=driver,
            )

            net = qs_tx.filter(leg__isnull=False, leg__driver=driver).aggregate(net=_wallet_net_expr()).get("net")
            if net is None:
                net = qs_tx.aggregate(net=_wallet_net_expr()).get("net")

            driver_income = Decimal(str(net or 0))
            if driver_income < 0:
                driver_income = Decimal("0")

            if driver_leg_amount_all > 0:
                driver_income_remaining = driver_leg_amount_all - driver_income
                if driver_income_remaining < 0:
                    driver_income_remaining = Decimal("0")

                driver_income_progress_pct = int((driver_income * 100) / driver_leg_amount_all)
                if driver_income_progress_pct < 0:
                    driver_income_progress_pct = 0
                if driver_income_progress_pct > 100:
                    driver_income_progress_pct = 100
    except Exception:
        pass

    payload = {
        "ok": True,
        "driver_id": getattr(driver, "id", None),

        "order": {
            "id": order.id,
            "code": getattr(order, "code", None),
            "status": order.status,
            "created_at": dt_iso(getattr(order, "created_at", None)),
            "pickup_time": dt_iso(getattr(order, "pickup_time", None)),
            "dropoff_time": dt_iso(getattr(order, "dropoff_time", None)),
            "wash_complete_time": dt_iso(getattr(order, "wash_complete_time", None)),
            "return_time": dt_iso(getattr(order, "return_time", None)),
            "delivered_time": dt_iso(getattr(order, "delivered_time", None)),
        },

        # ✅ timeline live (format UI)
        "order_times": {
            "pickup_time": dt_fr(getattr(order, "pickup_time", None)),
            "dropoff_time": dt_fr(getattr(order, "dropoff_time", None)),
            "wash_complete_time": dt_fr(getattr(order, "wash_complete_time", None)),
            "return_time": dt_fr(getattr(order, "return_time", None)),
            "delivered_time": dt_fr(getattr(order, "delivered_time", None)),
        },

        "legs": legs,

        "kpi": {
            "driver_income": float(driver_income or 0),
            "driver_leg_amount_all": float(driver_leg_amount_all or 0),
            "driver_leg_amount_done": float(driver_leg_amount_done or 0),
            "driver_income_remaining": float(driver_income_remaining or 0),
            "driver_income_progress_pct": int(driver_income_progress_pct or 0),
        },
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
        "connected_driver": _get_connected_driver(request),
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

