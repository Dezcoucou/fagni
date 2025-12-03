from decimal import Decimal
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count, Case, When, F
from django.http import Http404, HttpResponse

from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from .forms import WithdrawalRequestForm
from .models import (
    ReferralLink,
    ReferralCommission,
    WithdrawalRequest,
)
# Nouveau : on utilise le vrai wallet FAGNI
from wallets.models import Wallet, WalletTransaction
from wallets.models import (
    Wallet,
    WalletTransaction as CustomerWalletTransaction,  # vrai wallet FAGNI (wallet, direction, etc.)
)


# ----------------------------------------------------
#  Utilitaire simple : récupérer un "profil affilié"
#  (en prod, il faudra lier au user connecté)
# ----------------------------------------------------
def _get_current_profile(request):
    """
    Récupère le profil affilié "courant".

    TODO (plus tard) : le lier vraiment à request.user.
    Pour l'instant :
      - en prod : on pourra brancher sur le client connecté
      - en tests : on prend simplement le premier ReferralLink créé.
    """
    profile = ReferralLink.objects.order_by("id").first()
    if not profile:
        raise Http404("Aucun profil affilié disponible.")
    return profile


# ----------------------------------------------------
#  Dashboard affilié
# ----------------------------------------------------
def affiliate_dashboard(request):
    """
    Dashboard affilié (vue front) :
    - Solde wallet
    - Total commissions MLM
    - Liste des filleuls N1
    - Commandes ayant généré des commissions pour ce profil
    """
    profile = _get_current_profile(request)

    # === WALLET DU CLIENT AFFILIÉ ===
    from wallets.models import Wallet, WalletTransaction

    wallet = Wallet.objects.filter(
        owner_type="customer",
        customer=profile.customer,
    ).first()

    if wallet:
        wallet_balance = wallet.balance
    else:
        wallet_balance = Decimal("0.00")

    # Total commissions (mouvements mlm_commission sur SON wallet)
    if wallet:
        agg_comm = WalletTransaction.objects.filter(
            wallet=wallet,
            type="mlm_commission",
        ).aggregate(total=Sum("amount"))
        total_commissions = agg_comm["total"] or Decimal("0.00")
    else:
        total_commissions = Decimal("0.00")

    # === FILLEULS DIRECTS (N1) ===
    n1_children = profile.direct_referrals.all()

    # === COMMANDES QUI ONT GÉNÉRÉ UNE COMMISSION POUR CE PROFIL ===
    # On part de ReferralCommission (modèle MLM) où ce profil est bénéficiaire
    orders_mlm = (
        ReferralCommission.objects.filter(beneficiary_profile=profile)
        .select_related("order", "order__customer")
        .values(
            "order__id",
            "order__code",
            "order__created_at",
            "order__customer__name",
            "order__total",
        )
        .annotate(
            commission_totale=Sum("commission_amount"),
        )
        .order_by("-order__created_at")
    )

    # Dernières transactions MLM sur son wallet (si on veut garder le bloc historique)
    if wallet:
        transactions = (
            WalletTransaction.objects.filter(
                wallet=wallet,
                type="mlm_commission",
            )
            .select_related("order")
            .order_by("-created_at")[:20]
        )
    else:
        transactions = WalletTransaction.objects.none()

    # Lien de parrainage basique
    base_url = "https://fagni.app/invite/"
    referral_url = f"{base_url}{profile.referral_code}"

    context = {
        "profile": profile,
        "wallet_balance": wallet_balance,
        "total_commissions": total_commissions,
        "n1_children": n1_children,
        "transactions": transactions,
        "referral_url": referral_url,
        "orders_mlm": orders_mlm,
    }
    return render(request, "mlm/affiliate_dashboard.html", context)


# ----------------------------------------------------
#  Liste des demandes de retrait (affilié)
# ----------------------------------------------------
def affiliate_withdrawals(request):
    profile = _get_current_profile(request)

    wallet = Wallet.objects.filter(
        owner_type="customer",
        customer=profile.customer,
    ).first()

    wallet_balance = wallet.balance if wallet else Decimal("0.00")

    requests_qs = profile.withdrawal_requests.all().order_by("-created_at")

    context = {
        "profile": profile,
        "wallet_balance": wallet_balance,
        "withdrawals": requests_qs,
    }
    return render(request, "mlm/affiliate_withdrawals.html", context)


# ----------------------------------------------------
#  Création d’une demande de retrait (affilié)
# ----------------------------------------------------
def affiliate_withdrawal_request(request):
    profile = _get_current_profile(request)

    wallet = Wallet.objects.filter(
        owner_type="customer",
        customer=profile.customer,
    ).first()

    wallet_balance = wallet.balance if wallet else Decimal("0.00")

    if request.method == "POST":
        form = WithdrawalRequestForm(request.POST, profile=profile)

        if form.is_valid():
            wr = form.save(commit=False)
            wr.profile = profile
            wr.status = "pending"
            wr.save()

            messages.success(
                request,
                "Votre demande de retrait a été enregistrée et sera traitée par l'équipe FAGNI.",
            )
            return redirect("mlm:affiliate_withdrawals")
    else:
        form = WithdrawalRequestForm(profile=profile)

    context = {
        "profile": profile,
        "wallet_balance": wallet_balance,
        "form": form,
    }
    return render(request, "mlm/affiliate_withdrawal_form.html", context)


# ----------------------------------------------------
#  Dashboard Admin MLM (récap affiliés / commissions)
# ----------------------------------------------------
@staff_member_required
def admin_mlm_dashboard(request):
    """
    Dashboard Admin MLM :
    - Synthèse globale des commissions et wallets
    - Liste des affiliés avec stats (N1, total commissions, solde wallet, etc.)
    - Pagination sur la liste des affiliés
    """
    from wallets.models import Wallet, WalletTransaction as WalletTx

    # --- Agrégats globaux ---
    total_commissions = (
        WalletTx.objects.filter(type="mlm_commission", direction="in")
        .aggregate(total=Sum("amount"))
        .get("total")
        or Decimal("0.00")
    )

    total_wallet_balance = (
        Wallet.objects.filter(owner_type="customer")
        .aggregate(total=Sum("balance"))
        .get("total")
        or Decimal("0.00")
    )

    total_withdraw_requested = (
        WithdrawalRequest.objects.aggregate(total=Sum("amount")).get("total")
        or Decimal("0.00")
    )

    # --- Affiliés (profils MLM) ---
    raw_affiliates = (
        ReferralLink.objects.select_related("customer")
        .prefetch_related("direct_referrals")
        .all()
        .order_by("referral_code")
    )

    affiliates = []

    for profile in raw_affiliates:
        customer = profile.customer

        # N1 = filleuls directs
        nb_filleuls = profile.direct_referrals.count()

        wallet = None
        wallet_balance = Decimal("0.00")
        total_aff_commissions = Decimal("0.00")
        last_commission = None

        if customer:
            # Wallet du client (côté app wallets)
            wallet = (
                Wallet.objects.filter(
                    owner_type="customer",
                    customer=customer,
                )
                .order_by("id")
                .first()
            )

            if wallet:
                wallet_balance = wallet.balance

                tx_qs = (
                    WalletTx.objects.filter(
                        wallet=wallet,
                        type="mlm_commission",
                        direction="in",
                    )
                    .order_by("-created_at")
                )

                total_aff_commissions = (
                    tx_qs.aggregate(total=Sum("amount")).get("total")
                    or Decimal("0.00")
                )

                first_tx = tx_qs.first()
                if first_tx:
                    last_commission = first_tx.created_at

        # On prépare l'URL de détail seulement si le code est “propre”
        detail_url = None
        if profile.referral_code and "/" not in profile.referral_code:
            try:
                detail_url = reverse(
                    "mlm:affiliate_detail",
                    args=[profile.referral_code],
                )
            except Exception:
                detail_url = None

        affiliates.append(
            {
                "profile": profile,
                "customer": customer,
                "nb_filleuls": nb_filleuls,
                "wallet_balance": wallet_balance,
                "total_commissions": total_aff_commissions,
                "last_commission": last_commission,
                "detail_url": detail_url,
            }
        )

    # --- Pagination ---
    per_default = 25
    per_options = [25, 50, 100]

    per_str = request.GET.get("per", str(per_default))
    try:
        per_page = int(per_str)
    except ValueError:
        per_page = per_default

    if per_page not in per_options:
        per_page = per_default

    paginator = Paginator(affiliates, per_page)

    page = request.GET.get("page", 1)
    try:
        affiliate_page = paginator.page(page)
    except PageNotAnInteger:
        affiliate_page = paginator.page(1)
    except EmptyPage:
        affiliate_page = paginator.page(paginator.num_pages)

    context = {
        "total_commissions": total_commissions,
        "total_wallet_balance": total_wallet_balance,
        "total_withdraw_requested": total_withdraw_requested,
        "affiliate_page": affiliate_page,
        "paginator": paginator,
        "per": per_page,
        "per_options": per_options,
    }

    return render(request, "mlm/admin_dashboard.html", context)


#  Vue Finance MLM (si tu as un template dédié)
# ----------------------------------------------------
@staff_member_required
def finance_dashboard(request):
    total_commissions = WalletTransaction.objects.filter(
        type="mlm_commission"
    ).aggregate(total=Sum("amount"))["total"] or 0

    total_wallet = WalletTransaction.objects.aggregate(
        total=Sum("amount")
    )["total"] or 0

    total_withdraw_requests = WithdrawalRequest.objects.aggregate(
        total=Sum("amount")
    )["total"] or 0

    context = {
        "total_commissions": int(total_commissions),
        "total_wallet": int(total_wallet),
        "total_withdraw_requests": int(total_withdraw_requests),
    }
    return render(request, "mlm/finance_dashboard.html", context)


# ----------------------------------------------------
#  Liste Admin des demandes de retrait
# ----------------------------------------------------
@staff_member_required
def admin_withdrawals(request):
    """
    Liste et traitement des demandes de retrait :
    - actions : approuver / rejeter / marquer payé
    - pagination + petits KPIs
    """
    from wallets.models import WalletTransaction as WalletTx

    if request.method == "POST":
        action = request.POST.get("action")
        req_id = request.POST.get("request_id")
        withdrawal = get_object_or_404(WithdrawalRequest, pk=req_id)

        if action == "approve" and withdrawal.status == "pending":
            withdrawal.status = "approved"
            withdrawal.processed_at = timezone.now()
            withdrawal.processed_by = request.user
            withdrawal.save()
            messages.success(request, "Demande de retrait approuvée.")

        elif action == "reject" and withdrawal.status in ["pending", "approved"]:
            withdrawal.status = "rejected"
            withdrawal.processed_at = timezone.now()
            withdrawal.processed_by = request.user
            withdrawal.save()
            messages.warning(request, "Demande de retrait rejetée.")

        elif action == "mark_paid" and withdrawal.status == "approved":
            withdrawal.status = "paid"
            withdrawal.processed_at = timezone.now()
            withdrawal.processed_by = request.user

            # Écriture d'un mouvement de wallet (débit)
            WalletTx.objects.create(
                type="payout",
                direction="out",
                amount=withdrawal.amount,
                wallet=None,  # à brancher si tu relies les wallets affiliés ici
                order=None,
                description=f"Paiement retrait #{withdrawal.pk}",
            )

            withdrawal.save()
            messages.success(
                request,
                "Retrait marqué comme payé (mouvement comptable enregistré).",
            )

        return redirect("mlm:admin_withdrawals")

    # ---- queryset de base ----
    qs = (
        WithdrawalRequest.objects.select_related("profile", "profile__customer")
        .all()
        .order_by("status", "-created_at")
    )

    # ---- petits KPIs ----
    total_requested = qs.aggregate(total=Sum("amount")).get("total") or Decimal("0.00")
    pending_count = qs.filter(status="pending").count()
    approved_count = qs.filter(status="approved").count()
    paid_count = qs.filter(status="paid").count()
    rejected_count = qs.filter(status="rejected").count()

    stats = {
        "total_requested": total_requested,
        "pending_count": pending_count,
        "approved_count": approved_count,
        "paid_count": paid_count,
        "rejected_count": rejected_count,
    }

    # ---- pagination ----
    per_page = 25
    paginator = Paginator(qs, per_page)
    page_number = request.GET.get("page") or 1
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    context = {
        "stats": stats,
        "page_obj": page_obj,
        "withdrawals": page_obj.object_list,
        "paginator": paginator,
    }
    return render(request, "mlm/admin_withdrawals.html", context)


# ----------------------------------------------------
#  Page “Contrat & CGU affilié” (HTML)
# ----------------------------------------------------
def affiliate_legal(request):
    return render(request, "mlm/affiliate_legal.html")


# ----------------------------------------------------
#  (Optionnel) version PDF à terme
# ----------------------------------------------------
def affiliate_legal_pdf(request):
    """
    Placeholder : plus tard, on pourra générer un vrai PDF.
    Pour l'instant, on renvoie simplement la page HTML.
    """
    html = render(request, "mlm/affiliate_legal.html").content
    return HttpResponse(html, content_type="text/html; charset=utf-8")


# ----------------------------------------------------
#  Dashboard Global MLM (basé sur ReferralCommission + Wallets)
# ----------------------------------------------------
@staff_member_required
def global_mlm_dashboard(request):
    """
    Vue backoffice globale MLM :
    - Nb de profils affiliés
    - Total commissions générées (ReferralCommission)
    - Solde total des wallets MLM (Wallet owner_type='customer')
    - Top parrains par commissions cumulées
    """

    # 1) Stat global : nombre de profils
    total_profiles = ReferralLink.objects.count()

    # 2) Total des commissions générées (toutes commandes, tous niveaux)
    agg_comm = ReferralCommission.objects.aggregate(
        total=Sum("commission_amount")
    )
    total_commissions = int(agg_comm["total"] or 0)

    # 3) Solde total des wallets affiliés (wallets app)
    agg_wallets = Wallet.objects.filter(owner_type="customer").aggregate(
        total_balance=Sum("balance")
    )
    total_wallet_balance = agg_wallets["total_balance"] or Decimal("0.00")

    # 4) Top parrains par commissions cumulées
    top_sponsors = (
        ReferralCommission.objects
        .values(
            "beneficiary_profile__id",
            "beneficiary_profile__referral_code",
            "beneficiary_profile__customer__id",
            "beneficiary_profile__customer__name",
        )
        .annotate(
            total_commissions=Sum("commission_amount"),
            orders_count=Count("order", distinct=True),
        )
        .order_by("-total_commissions")[:10]
    )

    context = {
        "total_profiles": total_profiles,
        "total_commissions": total_commissions,
        "total_wallet_balance": total_wallet_balance,
        "top_sponsors": top_sponsors,
    }
    return render(request, "mlm/global_dashboard.html", context)


@staff_member_required
def affiliate_detail(request, referral_code):
    """
    Fiche détaillée d'un affilié :
    - Infos profil
    - Résumé parrainage
    - Filleuls directs N1
    - Commandes / commissions (avec pagination)
    """
    from decimal import Decimal
    from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
    from wallets.models import Wallet  # pour le solde portefeuille

    # --- Profil affilié ---
    profile = get_object_or_404(ReferralLink, referral_code=referral_code)

    # --- Transactions MLM (commissions pour cet affilié) ---
    # On passe par le wallet du client (et plus par profile=...)
    wallet_tx_qs = WalletTransaction.objects.filter(
        wallet__owner_type="customer",
        wallet__customer=profile.customer,
        type="mlm_commission",
    ).select_related("order").order_by("-created_at")

    # Total des commissions cumulées
    total_comm = wallet_tx_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    # --- Pagination des transactions ---
    page_number = request.GET.get("page", 1)
    paginator = Paginator(wallet_tx_qs, 25)  # 25 lignes par page

    try:
        tx_page = paginator.page(page_number)
    except PageNotAnInteger:
        tx_page = paginator.page(1)
    except EmptyPage:
        tx_page = paginator.page(paginator.num_pages)

    # --- Filleuls directs N1 ---
    n1_children = profile.direct_referrals.select_related("customer").all()

    # --- Portefeuille client (wallet côté customer) ---
    customer_wallet = None
    customer_wallet_balance = Decimal("0.00")

    if profile.customer:
        try:
            customer_wallet = Wallet.objects.get(
                owner_type="customer",
                customer=profile.customer,
            )
            customer_wallet_balance = customer_wallet.balance
        except Wallet.DoesNotExist:
            pass

    # Dernière transaction (pour affichage résumé si besoin)
    last_tx = wallet_tx_qs.first()

    context = {
        "profile": profile,
        "total_comm": total_comm,
        "tx_page": tx_page,
        "paginator": paginator,
        "n1_children": n1_children,
        "customer_wallet": customer_wallet,
        "customer_wallet_balance": customer_wallet_balance,
        "last_tx": last_tx,
    }

    return render(request, "mlm/affiliate_detail.html", context)
