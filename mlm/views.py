from decimal import Decimal
from datetime import timedelta

from django.shortcuts import render
from django.db.models import Sum, Count
from django.utils import timezone

from orders.models import Order
from mlm.models import ReferralCommission, WalletTransaction
from partners.models import LaundryPartner, DeliveryPartner


def finance_dashboard(request):
    """
    Dashboard financier FAGNI :
    - Synthèse commandes
    - Revenus FAGNI (service_fee)
    - Commissions MLM
    - Top partenaires
    - Filtre par période (all / 30j / 7j / today)
    """

    # 1) Gestion de la période
    period = request.GET.get("period", "all")
    now = timezone.now()
    start_date = None
    periode_label = "Toutes les périodes"

    if period == "30d":
        start_date = now - timedelta(days=30)
        periode_label = "30 derniers jours"
    elif period == "7d":
        start_date = now - timedelta(days=7)
        periode_label = "7 derniers jours"
    elif period == "today":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
        periode_label = "Aujourd'hui"

    # 2) Base queryset
    orders_qs = Order.objects.all()
    if start_date is not None:
        orders_qs = orders_qs.filter(created_at__gte=start_date)

    total_orders = orders_qs.count()

    # CA prestations (on utilise le champ 'total' stocké sur Order)
    total_revenue_prestation = (
        orders_qs.aggregate(s=Sum("total"))["s"] or Decimal("0.00")
    )

    # Revenus FAGNI (service_fee)
    total_service_fee = (
        orders_qs.aggregate(s=Sum("service_fee"))["s"] or Decimal("0.00")
    )

    # Total commissions MLM (on ne filtre pas par période pour l'instant,
    # mais on pourrait si on ajoutait un champ "created_at" sur ReferralCommission)
    total_mlm_commissions = (
        ReferralCommission.objects.aggregate(s=Sum("commission_amount"))["s"]
        or Decimal("0.00")
    )

    # Total crédité sur les wallets (type "mlm_commission")
    total_wallet_credits = (
        WalletTransaction.objects.filter(type="mlm_commission")
        .aggregate(s=Sum("amount"))["s"]
        or Decimal("0.00")
    )

    # Revenu net FAGNI = service_fee – ce qui est redistribué en MLM
    net_fagni_revenue = (total_service_fee - Decimal(total_wallet_credits)).quantize(
        Decimal("0.01")
    )

    # Top blanchisseries par nombre de commandes (sur la période choisie)
    top_laundries = (
        LaundryPartner.objects.annotate(
            nb_orders=Count("orders")
        )
        .filter(nb_orders__gt=0)
        .order_by("-nb_orders")[:5]
    )

    # Top livreurs par nombre de commandes (sur la période choisie)
    top_deliveries = (
        DeliveryPartner.objects.annotate(
            nb_orders=Count("orders")
        )
        .filter(nb_orders__gt=0)
        .order_by("-nb_orders")[:5]
    )

    context = {
        "period": period,
        "periode_label": periode_label,

        "total_orders": total_orders,
        "total_revenue_prestation": total_revenue_prestation,
        "total_service_fee": total_service_fee,
        "total_mlm_commissions": total_mlm_commissions,
        "total_wallet_credits": total_wallet_credits,
        "net_fagni_revenue": net_fagni_revenue,

        "top_laundries": top_laundries,
        "top_deliveries": top_deliveries,
    }

    return render(request, "mlm/finance_dashboard.html", context)
