from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils import timezone

from orders.models import Order, Customer

DECIMAL_ZERO = Decimal("0.00")


@login_required
def index(request):
    """
    Petit tableau de bord global très simple.
    Utilisé par /dashboard/
    """
    today = timezone.localdate()

    total_orders = Order.objects.count()
    done_today = Order.objects.filter(status="done", created_at__date=today).count()
    total_revenue = (
        Order.objects.aggregate(total=Coalesce(Sum("total"), DECIMAL_ZERO))["total"]
        or DECIMAL_ZERO
    )

    context = {
        "today": today,
        "total_orders": total_orders,
        "done_today": done_today,
        "total_revenue": total_revenue,
    }
    return render(request, "dashboard/index.html", context)


@login_required
def analytics(request):
    """
    Vue analytics simple :
    - commandes par jour (7 derniers jours)
    - CA par jour (7 derniers jours)
    """
    today = timezone.localdate()
    DAYS = 7

    labels = []
    orders_count = []
    revenues = []

    for i in range(DAYS - 1, -1, -1):
        day = today - timezone.timedelta(days=i)
        day_qs = Order.objects.filter(created_at__date=day)

        labels.append(day.strftime("%d/%m"))
        orders_count.append(day_qs.count())
        revenues.append(
            float(
                day_qs.aggregate(
                    total=Coalesce(Sum("total"), DECIMAL_ZERO)
                )["total"]
                or 0
            )
        )

    context = {
        "labels": labels,
        "orders_count": orders_count,
        "revenues": revenues,
        "days": DAYS,
    }
    return render(request, "dashboard/analytics.html", context)


@login_required
def top_clients(request):
    """
    Top clients côté dashboard global :
    - basé sur Order.total
    """
    top_clients_qs = (
        Order.objects.filter(customer__isnull=False)
        .values("customer__id", "customer__name", "customer__phone")
        .annotate(
            total_spent=Coalesce(Sum("total"), DECIMAL_ZERO),
            orders_count=Count("id"),
        )
        .order_by("-total_spent")[:20]
    )

    # On convertit en liste pour plus de confort dans le template
    top_clients_list = list(top_clients_qs)

    context = {
        "top_clients": top_clients_list,
    }
    return render(request, "dashboard/top_clients.html", context)
