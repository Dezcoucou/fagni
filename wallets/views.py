from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from django.shortcuts import get_object_or_404, render, redirect
from django.db.models.functions import Coalesce
from django.db.models import Sum, Case, When, F, Value, DecimalField
from django.contrib.auth.decorators import login_required
from django.urls import reverse
from orders.models import Customer
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from .models import Wallet, WalletTransaction, WithdrawalRequest
from partners.models import DeliveryPartner
from django.contrib import messages
from django.utils import timezone
from .services import get_or_create_wallet_for_delivery_partner


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


def _get_current_driver(request) -> DeliveryPartner | None:
    """
    Récupère le livreur courant à partir de driver_id dans l'URL.
    Exemple : /wallets/driver/me/?driver_id=12
    """
    driver_id = request.GET.get("driver_id")
    if not driver_id:
        return None

    try:
        return DeliveryPartner.objects.get(pk=driver_id)
    except DeliveryPartner.DoesNotExist:
        return None


@login_required
def driver_wallet_dashboard(request):
    """
    Dashboard du wallet livreur :
    - solde actuel
    - total gagné ce mois-ci (entrées)
    - dernières transactions
    - création de demande de retrait
    """

    driver_id = request.GET.get("driver_id")

    if not driver_id:
        context = {
            "error_message": (
                "Aucun livreur sélectionné. Merci d'accéder à cette page "
                "depuis l'app livreur ou d'ajouter ?driver_id=ID dans l'URL."
            )
        }
        return render(request, "wallets/driver_wallet_dashboard.html", context)

    driver = DeliveryPartner.objects.filter(pk=driver_id).first()
    if not driver:
        context = {
            "error_message": "Livreur introuvable pour cet identifiant."
        }
        return render(request, "wallets/driver_wallet_dashboard.html", context)

    # Wallet du livreur
    wallet = get_or_create_wallet_for_delivery_partner(driver)

    # Gestion du POST : demande de retrait
    if request.method == "POST":
        amount_str = request.POST.get("amount", "").strip() or "0"
        try:
            amount = Decimal(amount_str)
        except Exception:
            messages.error(request, "Montant invalide.")
            return redirect(f"{reverse('wallets:driver_wallet_dashboard')}?driver_id={driver.id}")

        if amount <= 0:
            messages.error(request, "Le montant doit être strictement positif.")
            return redirect(f"{reverse('wallets:driver_wallet_dashboard')}?driver_id={driver.id}")

        if amount > wallet.balance:
            messages.error(
                request,
                "Le montant demandé dépasse ton solde disponible."
            )
            return redirect(f"{reverse('wallets:driver_wallet_dashboard')}?driver_id={driver.id}")

        # Création de la demande de retrait (statut en attente)
        WithdrawalRequest.objects.create(
            wallet=wallet,
            delivery_partner=driver,
            requested_by=request.user if request.user.is_authenticated else None,
            amount=amount,
            status="pending",
        )

        messages.success(
            request,
            "Ta demande de paiement a été enregistrée. "
            "Elle sera traitée par l'équipe FAGNI."
        )

        return redirect(f"{reverse('wallets:driver_wallet_dashboard')}?driver_id={driver.id}")

    # GET : affichage des infos
    tx_qs = wallet.transactions.all().order_by("-created_at")[:50]

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_in_qs = wallet.transactions.filter(
        created_at__gte=month_start,
        direction="in",
    )
    month_earnings = (
        month_in_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
    ).quantize(Decimal("0.01"))

    # Demandes de retrait
    pending_withdrawals = wallet.withdrawals.filter(status="pending").order_by("-created_at")
    last_withdrawals = wallet.withdrawals.all().order_by("-created_at")[:10]

    context = {
        "driver": driver,
        "wallet": wallet,
        "transactions": tx_qs,
        "month_earnings": month_earnings,
        "month_start": month_start,
        "pending_withdrawals": pending_withdrawals,
        "last_withdrawals": last_withdrawals,
    }

    return render(request, "wallets/driver_wallet_dashboard.html", context)
