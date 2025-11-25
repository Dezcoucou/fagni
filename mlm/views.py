from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Sum, Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import WithdrawalRequestForm
from .models import (
    ReferralLink,
    WalletTransaction,
    ReferralCommission,
    WithdrawalRequest,
)


# ----------------------------------------------------
#  Utilitaire simple : récupérer un "profil affilié"
#  (en prod, il faudra lier au user connecté)
# ----------------------------------------------------
def _get_current_profile(request):
    """
    Version de développement : on prend le 1er profil existant.
    À brancher plus tard sur le user authentifié.
    """
    profile = ReferralLink.objects.first()
    if not profile:
        raise Http404("Aucun profil d'affilié n'est encore configuré.")
    return profile


# ----------------------------------------------------
#  Dashboard affilié
# ----------------------------------------------------
def affiliate_dashboard(request):
    profile = _get_current_profile(request)

    # Solde wallet
    agg_wallet = WalletTransaction.objects.filter(
        profile=profile
    ).aggregate(total=Sum("amount"))
    wallet_balance = int(agg_wallet["total"] or 0)

    # Total commissions (toutes transactions de type mlm_commission)
    agg_comm = WalletTransaction.objects.filter(
        profile=profile,
        type="mlm_commission",
    ).aggregate(total=Sum("amount"))
    total_commissions = int(agg_comm["total"] or 0)

    # Filleuls N1 – pour l’instant sans annotation avancée
    n1_children = profile.direct_referrals.all()

    # Dernières transactions MLM
    transactions = WalletTransaction.objects.filter(
        profile=profile,
        type="mlm_commission",
    ).select_related("order").order_by("-created_at")[:20]

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
    }
    return render(request, "mlm/affiliate_dashboard.html", context)


# ----------------------------------------------------
#  Liste des demandes de retrait (affilié)
# ----------------------------------------------------
def affiliate_withdrawals(request):
    profile = _get_current_profile(request)

    agg_wallet = WalletTransaction.objects.filter(
        profile=profile
    ).aggregate(total=Sum("amount"))
    wallet_balance = int(agg_wallet["total"] or 0)

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

    agg_wallet = WalletTransaction.objects.filter(
        profile=profile
    ).aggregate(total=Sum("amount"))
    wallet_balance = int(agg_wallet["total"] or 0)

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
    # Affiliés + stats simples
    affiliates = (
        ReferralLink.objects.all()
        .annotate(
            nb_commissions=Count("commissions"),
            total_wallet=Sum("wallet_transactions__amount"),
        )
        .order_by("-total_wallet")
    )

    total_commissions = WalletTransaction.objects.filter(
        type="mlm_commission"
    ).aggregate(total=Sum("amount"))["total"] or 0

    total_withdraw_requested = WithdrawalRequest.objects.aggregate(
        total=Sum("amount")
    )["total"] or 0

    context = {
        "affiliates": affiliates,
        "total_commissions": int(total_commissions),
        "total_withdraw_requested": int(total_withdraw_requested),
    }
    return render(request, "mlm/admin_dashboard.html", context)


# ----------------------------------------------------
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
    Liste et traitement basique des demandes de retrait.
    Actions : approuver, rejeter, marquer comme payée.
    """
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

        elif action == "mark_paid" and withdrawal.status in ["approved"]:
            withdrawal.status = "paid"
            withdrawal.processed_at = timezone.now()
            withdrawal.processed_by = request.user

            # Écriture d'un mouvement de wallet (débit)
            WalletTransaction.objects.create(
                profile=withdrawal.profile,
                type="payout",
                amount=-int(withdrawal.amount),
                order=None,
                description=f"Paiement retrait #{withdrawal.pk}",
            )

            withdrawal.save()
            messages.success(request, "Retrait marqué comme payé et wallet mis à jour.")

        return redirect("mlm:admin_withdrawals")

    withdrawals = (
        WithdrawalRequest.objects.select_related("profile", "profile__customer")
        .all()
        .order_by("status", "-created_at")
    )

    context = {
        "withdrawals": withdrawals,
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
