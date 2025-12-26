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
    Dashboard du wallet livreur.

    IMPORTANT :
    - DeliveryPartner n'est PAS lié à User dans le modèle (pas de champ user).
    - Donc on se base sur ?driver_id= pour déterminer le livreur.
    - Staff : peut choisir n'importe quel driver_id
    - Non-staff : driver_id est requis (sinon page d'erreur)
    """
    user = request.user
    selected_driver_id = (request.GET.get("driver_id") or "").strip()

    driver = None
    if selected_driver_id:
        driver = DeliveryPartner.objects.filter(pk=selected_driver_id).first()

    # Sécurité : sans driver_id, on ne peut pas deviner le livreur
    if not driver:
        context = {
            "error_message": (
                "Livreur non identifié. Ouvre d’abord l’app livreur, puis clique sur Wallet "
                "(le lien doit contenir ?driver_id=...)."
            )
        }
        return render(request, "orders/driver_wallet.html", context)

    # ------------------------------------------------------------
    # 🔒 VERROUILLAGE WALLET : non-staff -> wallet uniquement "à lui"
    # Comme DeliveryPartner n'est pas lié à User, on vérifie par EMAIL si possible.
    # (Si pas d'email côté driver, on laisse passer mais au moins le détail commande est verrouillé.)
    # ------------------------------------------------------------
    if not request.user.is_staff:
        user_email = (getattr(request.user, "email", "") or "").strip().lower()
        driver_email = (getattr(driver, "email", "") or "").strip().lower()

        if user_email and driver_email and user_email != driver_email:
            return render(request, "orders/driver_wallet.html", {
                "error_message": "Accès refusé : ce wallet n’est pas associé à ton compte."
            })

    # Wallet du livreur
    wallet = get_or_create_wallet_for_delivery_partner(driver)

    # POST : demande de retrait
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
            messages.error(request, "Le montant demandé dépasse ton solde disponible.")
            return redirect(f"{reverse('wallets:driver_wallet_dashboard')}?driver_id={driver.id}")

        WithdrawalRequest.objects.create(
            wallet=wallet,
            delivery_partner=driver,
            requested_by=request.user if request.user.is_authenticated else None,
            amount=amount,
            status="pending",
        )

        messages.success(
            request,
            "Ta demande de paiement a été enregistrée. Elle sera traitée par l'équipe FAGNI."
        )
        return redirect(f"{reverse('wallets:driver_wallet_dashboard')}?driver_id={driver.id}")

    # GET : affichage des infos
    tx_qs = wallet.transactions.all().order_by("-created_at")[:50]

    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_in_qs = wallet.transactions.filter(created_at__gte=month_start, direction="in")
    month_earnings = (month_in_qs.aggregate(s=Sum("amount"))["s"] or Decimal("0.00")).quantize(Decimal("0.01"))

    pending_withdrawals = wallet.withdrawals.filter(status="pending").order_by("-created_at")
    last_withdrawals = wallet.withdrawals.all().order_by("-created_at")[:10]

    total_credited = wallet.transactions.filter(direction="in").aggregate(s=Sum("amount"))["s"] or Decimal("0.00")
    total_debited  = wallet.transactions.filter(direction="out").aggregate(s=Sum("amount"))["s"] or Decimal("0.00")

    context = {
        "driver": driver,
        "wallet": wallet,
        "transactions": tx_qs,
        "month_earnings": month_earnings,
        "month_start": month_start,
        "pending_withdrawals": pending_withdrawals,
        "last_withdrawals": last_withdrawals,
        "selected_driver_id": selected_driver_id,
        "total_credited": total_credited,
        "total_debited": total_debited,
    }
    return render(request, "orders/driver_wallet.html", context)
