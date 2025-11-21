import csv
from decimal import Decimal
from datetime import timedelta

from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
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

# Champ décimal générique pour les expressions
DEC = DecimalField(max_digits=12, decimal_places=2)


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
    qs = (
        Order.objects
        .select_related("customer", "laundry_partner", "delivery_partner")
        .order_by("-created_at")
    )

    current_status = request.GET.get("status", "all")
    if current_status in ("pending", "in_progress", "done", "canceled"):
        qs = qs.filter(status=current_status)

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
        "current_status": current_status,
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


# ============================================================
#  LISTE DES COMMANDES D'UN CLIENT
# ============================================================
def orders_by_customer(request, customer_id):
    orders = (
        _annotate_totals(Order.objects.filter(customer_id=customer_id))
        .order_by("-id")[:200]
    )
    return render(
        request,
        "orders/orders_by_customer.html",
        {"orders": orders, "customer_id": customer_id},
    )


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
    """
    service_categories = ServiceCategory.objects.all()
    service_items = ServiceItem.objects.select_related("category").all()

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
        "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
    }

    # --- AJOUT : paramètres logistiques pour l'affichage dans le formulaire ---
    logi = getattr(settings, "FAGNI_LOGISTICS", {})
    context.update({
        "delivery_min_fee": logi.get("client_min_fee", 1000),
        "delivery_price_per_km": logi.get("client_price_per_km", 150),
        "delivery_fixed_fee": logi.get("client_fixed_fee", 300),
    })
    # --- FIN AJOUT ---

    if request.method == "POST":
        phone = request.POST.get("client_phone", "").strip()
        name = request.POST.get("client_name", "").strip()
        address = request.POST.get("client_address", "").strip()
        lat_raw = request.POST.get("client_lat", "").strip()
        lng_raw = request.POST.get("client_lng", "").strip()

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
        # On met à jour dans tous les cas
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

        delivery_partner = auto_assign_delivery(order)  # un seul argument
        if delivery_partner:
            order.delivery_partner = delivery_partner

        # 7) Calcul des frais de livraison (si une blanchisserie est affectée)
        if order.laundry_partner:
            order.delivery_fee = order.compute_delivery_fee()

        # 8) Sauvegarde finale (recalcule total + service_fee)
        order.save()

        # Redirection : liste des commandes
        return redirect("orders:list")

    # GET => affichage simple du formulaire
    return render(request, "orders/create.html", context)


# ============================================================
#  PLACEHOLDERS ÉDITION / SUPPRESSION
# ============================================================
def edit(request):
    return HttpResponse("edit - placeholder", content_type="text/plain; charset=utf-8")


def delete(request):
    return HttpResponse("delete - placeholder", content_type="text/plain; charset=utf-8")


# ============================================================
#  DÉTAIL COMMANDE
# ============================================================
def detail(request, order_id):
    order = get_object_or_404(
        Order.objects.select_related("customer").prefetch_related("items"),
        pk=order_id,
    )

    context = {
        "order": order,
        "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
    }
    return render(request, "orders/detail.html", context)


# ============================================================
#  MISE À JOUR COMMANDE
# ============================================================
@login_required
def update(request, order_id):
    """
    Édition d'une commande existante :
    - mise à jour client
    - mise à jour / création / suppression des lignes
    - ajout éventuel de nouvelles photos sur chaque ligne (photos_0, photos_1, ...)
    ⚠ On NE supprime PLUS systématiquement toutes les lignes : on ne supprime
      que celles qui ne sont plus présentes dans le formulaire.
    """
    order = get_object_or_404(Order, pk=order_id)
    customer = order.customer

    if request.method == "POST":
        # 1) Données client
        name = (request.POST.get("client_name") or "").strip()
        phone = (request.POST.get("client_phone") or "").strip()
        address = (request.POST.get("client_address") or "").strip()

        if not name:
            logi = getattr(settings, "FAGNI_LOGISTICS", {})
            context = {
                "order": order,
                "error": "Le nom du client est obligatoire.",
                "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
                "service_categories": ServiceCategory.objects.all(),
                "service_items": ServiceItem.objects.filter(is_active=True),
                "delivery_min_fee": logi.get("client_min_fee", 1000),
                "delivery_price_per_km": logi.get("client_price_per_km", 150),
                "delivery_fixed_fee": logi.get("client_fixed_fee", 300),
            }
            return render(request, "orders/update.html", context)

        # 2) Mise à jour du client
        changed = False
        if customer.name != name:
            customer.name = name
            changed = True
        if phone and customer.phone != phone:
            customer.phone = phone
            changed = True
        if address and customer.address != address:
            customer.address = address
            changed = True
        if changed:
            customer.save()

        # 3) Mise à jour éventuelle de la géoloc
        lat = request.POST.get("client_lat")
        lng = request.POST.get("client_lng")
        if lat and lng:
            try:
                customer.latitude = Decimal(lat)
                customer.longitude = Decimal(lng)
                customer.save()
            except Exception:
                pass

        # 4) Récupération des tableaux (lignes)
        service_ids = request.POST.getlist("service_id[]") or request.POST.getlist("service_id")
        designations = request.POST.getlist("designation[]") or request.POST.getlist("designation")
        quantities = request.POST.getlist("quantity[]") or request.POST.getlist("quantity")
        unit_prices = request.POST.getlist("unit_price[]") or request.POST.getlist("unit_price")

        # IDs des lignes existantes (TR déjà en base)
        raw_item_ids = request.POST.getlist("item_id[]")
        existing_ids = []
        for rid in raw_item_ids:
            try:
                existing_ids.append(int(rid))
            except (ValueError, TypeError):
                continue

        # 4.a Supprimer les lignes qui ont disparu du formulaire
        if existing_ids:
            order.items.exclude(pk__in=existing_ids).delete()
        else:
            # si aucun ID renvoyé → toutes les anciennes lignes sont supprimées côté front
            order.items.all().delete()

        # 4.b Charger les lignes restantes en mémoire
        existing_items_by_id = {it.id: it for it in order.items.all()}
        created_or_updated = []  # (row_index, item)

        row_count = len(service_ids)

        for row_index in range(row_count):
            sid = service_ids[row_index] if row_index < len(service_ids) else ""
            designation = (designations[row_index] if row_index < len(designations) else "").strip()
            qty_raw = quantities[row_index] if row_index < len(quantities) else "0"
            price_raw = unit_prices[row_index] if row_index < len(unit_prices) else "0"

            # normalisation
            if isinstance(qty_raw, str):
                qty_raw = qty_raw.replace(",", ".")
            if isinstance(price_raw, str):
                price_raw = price_raw.replace(",", ".")

            try:
                qty = int(qty_raw or "0")
            except (ValueError, TypeError):
                qty = 0

            try:
                price = Decimal(str(price_raw or "0"))
            except Exception:
                price = Decimal("0")

            if qty <= 0 or price <= 0:
                # ligne vide → ignorée
                continue

            # Service catalogue (facultatif)
            service_obj = None
            if sid:
                try:
                    service_obj = ServiceItem.objects.get(pk=sid)
                except ServiceItem.DoesNotExist:
                    service_obj = None

            # ---------- LIGNE EXISTANTE OU NOUVELLE ? ----------
            if row_index < len(existing_ids):
                # Mise à jour d'une ligne existante
                item_id = existing_ids[row_index]
                item = existing_items_by_id.get(item_id)
                if not item:
                    item = OrderItem(order=order, service=service_obj)

                item.service = service_obj
                item.designation = designation or (service_obj.name if service_obj else "")
                item.quantity = qty
                item.unit_price = price
                item.save()  # total recalculé dans OrderItem.save()
            else:
                # Nouvelle ligne ajoutée
                item = OrderItem.objects.create(
                    order=order,
                    service=service_obj,
                    designation=designation or (service_obj.name if service_obj else ""),
                    quantity=qty,
                    unit_price=price,
                )

            created_or_updated.append((row_index, item))

        if not created_or_updated:
            logi = getattr(settings, "FAGNI_LOGISTICS", {})
            context = {
                "order": order,
                "error": "Ajoute au moins une ligne de prestation.",
                "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
                "service_categories": ServiceCategory.objects.all(),
                "service_items": ServiceItem.objects.filter(is_active=True),
                "delivery_min_fee": logi.get("client_min_fee", 1000),
                "delivery_price_per_km": logi.get("client_price_per_km", 150),
                "delivery_fixed_fee": logi.get("client_fixed_fee", 300),
            }
            return render(request, "orders/update.html", context)

        # 5) Photos : conserver les anciennes, ajouter les nouvelles
        for row_index, item in created_or_updated:
            field_name = f"photos_{row_index}"
            files = request.FILES.getlist(field_name)
            for f in files:
                OrderItemPhoto.objects.create(
                    order_item=item,
                    image=f,
                )

        # 6) Recalcul des totaux (total + service_fee via save())
        order.save()

        # 7) Calcul / recalcul livraison via le modèle (Haversine ou minimum)
        delivery_fee = order.compute_delivery_fee()
        order.delivery_fee = delivery_fee
        order.save(
            update_fields=[
                "distance_km",
                "driver_logistic_cost",
                "logistic_margin",
                "delivery_fee",
            ]
        )

        return redirect("orders:detail", order_id=order.id)

    # GET : affichage du formulaire pré-rempli
    logi = getattr(settings, "FAGNI_LOGISTICS", {})
    context = {
        "order": order,
        "GOOGLE_MAPS_API_KEY": getattr(settings, "GOOGLE_MAPS_API_KEY", ""),
        "service_categories": ServiceCategory.objects.all(),
        "service_items": ServiceItem.objects.filter(is_active=True),
        "delivery_min_fee": logi.get("client_min_fee", 1000),
        "delivery_price_per_km": logi.get("client_price_per_km", 150),
        "delivery_fixed_fee": logi.get("client_fixed_fee", 300),
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
    - totaux globaux calculés comme somme des lignes

    => garantit :
        * Total livraison facturée = somme des livraisons par livreur
        * Coût logistique = somme des coûts livreurs par livreur
        * Marge FAGNI = total_delivery_global - total_driver_cost_global
        * Distance totale = somme des distances par livreur
    """

    # Base : uniquement les commandes avec un livreur renseigné
    base_qs = (
        Order.objects
        .select_related("delivery_partner")
        .filter(delivery_partner__isnull=False)
    )

    # 1) Stats par livreur
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
        .order_by("delivery_partner__name")
    )

    driver_stats = []

    # 2) Totaux globaux calculés comme somme des lignes
    global_nb_orders = 0
    global_total_delivery = Decimal("0.00")
    global_total_driver_cost = Decimal("0.00")
    global_total_distance = Decimal("0.00")
    global_total_photos = 0

    for row in raw_stats:
        delivery = row["total_delivery"] or Decimal("0.00")
        cost = row["total_driver_cost"] or Decimal("0.00")
        distance = row["total_distance"] or Decimal("0.00")
        photos = row["photos_count"] or 0
        nb_orders = row["nb_orders"] or 0

        # Marge calculée LIGNE PAR LIGNE
        margin = delivery - cost
        row["computed_margin"] = margin

        driver_stats.append(row)

        # Accumulation des totaux
        global_nb_orders += nb_orders
        global_total_delivery += delivery
        global_total_driver_cost += cost
        global_total_distance += distance
        global_total_photos += photos

    # Marge globale = différence entre total livraison et total coût
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
    }
    return render(request, "orders/ops_drivers.html", context)


# ============================================================
#  TOP CLIENTS CSV
# ============================================================
def export_top_clients_csv(request):
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
            montant_total=Coalesce(F("total"), items_total, output_field=DEC),
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
