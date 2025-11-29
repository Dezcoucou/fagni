from decimal import Decimal
import json

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, DecimalField, F, ExpressionWrapper
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone

from orders.models import Order


# On fixe une base décimale propre pour tous les agrégats
DECIMAL_ZERO = Decimal("0.00")


@login_required
def index(request):
    """
    Petit tableau de bord global très simple.
    Utilisé par /dashboard/
    """
    today = timezone.localdate()

    # Commandes totales
    total_orders = Order.objects.count()

    # Commandes du jour
    today_qs = Order.objects.filter(created_at__date=today)
    today_orders = today_qs.count()
    today_revenue = (
        today_qs.aggregate(
            total=Coalesce(
                Sum(
                    "total",
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                DECIMAL_ZERO,
            )
        )["total"]
        or DECIMAL_ZERO
    )

    # CA cumulé
    total_revenue = (
        Order.objects.aggregate(
            total=Coalesce(
                Sum(
                    "total",
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                DECIMAL_ZERO,
            )
        )["total"]
        or DECIMAL_ZERO
    )

    # Restant dû estimé si amount_due existe en propriété Python
    remaining_due = Decimal("0.00")
    for o in Order.objects.all():
        if hasattr(o, "amount_due") and o.amount_due:
            remaining_due += Decimal(o.amount_due)

    context = {
        "today": today,
        "total_orders": total_orders,
        "today_orders": today_orders,
        "today_revenue": today_revenue,
        "total_revenue": total_revenue,
        "remaining_due": remaining_due,
    }
    return render(request, "dashboard/index.html", context)


@login_required
def analytics(request):
    """
    Vue analytics FAGNI :
    - KPIs sur une période (par défaut 7 jours)
    - séries 'commandes/jour' et 'CA/jour' pour Chart.js
    - top prestations (montant total vendu)
    """
    today = timezone.localdate()

    # Période en jours (7 ou 30 typiquement)
    try:
        days = int(request.GET.get("days", 7))
    except ValueError:
        days = 7
    if days not in (7, 30):
        days = 7

    date_from = today - timezone.timedelta(days=days - 1)
    date_to = today

    # Queryset filtré sur la période (on exclut les annulées)
    qs = Order.objects.filter(
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    ).exclude(status="canceled")

    # --- KPIs globaux sur la période ---

    agg = qs.aggregate(
        total_orders=Count("id"),
        total_ttc=Coalesce(
            Sum(
                "total",
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
            DECIMAL_ZERO,
        ),
    )

    total_orders_period = agg["total_orders"] or 0
    total_ttc_period = agg["total_ttc"] or DECIMAL_ZERO

    # commandes terminées
    done_count = qs.filter(status="done").count()
    completion_rate = (
        float(done_count) / float(total_orders_period) * 100.0
        if total_orders_period > 0
        else 0.0
    )

    # panier moyen
    avg_basket = (
        total_ttc_period / Decimal(total_orders_period)
        if total_orders_period > 0
        else DECIMAL_ZERO
    )

    # --- Séries journalières pour les graphiques ---

    labels = []
    orders_series = []
    revenue_series = []

    for i in range(days - 1, -1, -1):
        day = today - timezone.timedelta(days=i)
        day_qs = qs.filter(created_at__date=day)

        labels.append(day.strftime("%d/%m"))

        count_day = day_qs.count()
        orders_series.append(count_day)

        rev_day = (
            day_qs.aggregate(
                total=Coalesce(
                    Sum(
                        "total",
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    ),
                    DECIMAL_ZERO,
                )
            )["total"]
            or DECIMAL_ZERO
        )
        revenue_series.append(float(rev_day))

    # --- Top prestations (par désignation) sur la période ---

    # On reconstruit le total ligne = quantity * unit_price
    line_total_expr = ExpressionWrapper(
        F("items__quantity") * F("items__unit_price"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )

    top_services_qs = (
        qs.filter(items__isnull=False)
        .annotate(
            service_label=F("items__designation"),
            line_total=line_total_expr,
        )
        .values("service_label")
        .annotate(
            total=Coalesce(
                Sum(
                    "line_total",
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                DECIMAL_ZERO,
            )
        )
        .order_by("-total")[:10]
    )

    top_services = list(top_services_qs)

    context = {
        "date_from": date_from,
        "date_to": date_to,
        "days": days,
        "total_orders_period": total_orders_period,
        "total_ttc_period": total_ttc_period,
        "avg_basket": avg_basket,
        "completion_rate": completion_rate,
        "labels": labels,
        # versions prêtes pour Chart.js
        "labels_json": json.dumps(labels, ensure_ascii=False),
        "orders_series_json": json.dumps(orders_series),
        "revenue_series_json": json.dumps(revenue_series),
        "top_services": top_services,
    }
    return render(request, "dashboard/analytics.html", context)


@login_required
def top_clients(request):
    """
    Top clients côté dashboard global :
    - basé sur Order.total (TTC)
    """
    top_clients_qs = (
        Order.objects.filter(customer__isnull=False)
        .values("customer__id", "customer__name", "customer__phone")
        .annotate(
            total_spent=Coalesce(
                Sum(
                    "total",
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                DECIMAL_ZERO,
            ),
            total_orders=Count("id"),
        )
        .order_by("-total_spent")[:20]
    )

    top_clients_list = list(top_clients_qs)

    context = {
        "top_clients": top_clients_list,
    }
    return render(request, "dashboard/top_clients.html", context)
