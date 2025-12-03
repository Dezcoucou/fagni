from decimal import Decimal, ROUND_HALF_UP
from django.shortcuts import get_object_or_404, render
from django.db.models.functions import Coalesce
from django.db.models import Sum, Case, When, F, Value, DecimalField
from orders.models import Customer
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from .models import Wallet, WalletTransaction


DEC = DecimalField(max_digits=12, decimal_places=2)


def customer_wallet_detail(request, customer_id):
    """
    Détail du portefeuille d'un client :
    - solde
    - historique des transactions (avec pagination)
    """
    customer = get_object_or_404(Customer, pk=customer_id)

    wallet = Wallet.objects.filter(
        owner_type="customer",
        customer=customer,
    ).first()

    if wallet:
        tx_qs = (
            WalletTransaction.objects
            .filter(wallet=wallet)
            .select_related("order")
            .order_by("-created_at")
        )
        balance = wallet.balance
    else:
        tx_qs = WalletTransaction.objects.none()
        balance = Decimal("0.00")

    # --- Pagination ---
    page_number = request.GET.get("page", 1)
    paginator = Paginator(tx_qs, 25)  # 25 lignes par page

    try:
        transactions_page = paginator.page(page_number)
    except PageNotAnInteger:
        transactions_page = paginator.page(1)
    except EmptyPage:
        transactions_page = paginator.page(paginator.num_pages)

    context = {
        "customer": customer,
        "wallet": wallet,
        "balance": balance,
        "transactions_page": transactions_page,
    }
    return render(request, "wallets/customer_wallet_detail.html", context)
