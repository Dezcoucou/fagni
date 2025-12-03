from django.urls import path

from .views import (
    affiliate_dashboard,
    affiliate_withdrawals,
    affiliate_withdrawal_request,
    admin_mlm_dashboard,
    finance_dashboard,
    admin_withdrawals,
    affiliate_legal,
    affiliate_legal_pdf,
    global_mlm_dashboard,
    affiliate_detail,  # 👈 important
)

app_name = "mlm"

urlpatterns = [
    # Dashboard affilié (côté parrain)
    path("affiliate/", affiliate_dashboard, name="affiliate_dashboard"),
    path(
        "affiliate/withdrawals/",
        affiliate_withdrawals,
        name="affiliate_withdrawals",
    ),
    path(
        "affiliate/withdrawals/new/",
        affiliate_withdrawal_request,
        name="affiliate_withdrawal_request",
    ),

    # Dashboard global MLM (admin FAGNI)
    path("global/", global_mlm_dashboard, name="global_dashboard"),

    # Détail d’un affilié (admin FAGNI ou staff)
    path(
        "affiliate/<str:referral_code>/",
        affiliate_detail,          # 👈 ici on utilise bien affiliate_detail
        name="affiliate_detail",
    ),

    # Vues admin MLM
    path("admin/dashboard/", admin_mlm_dashboard, name="admin_dashboard"),
    path("admin/finance/", finance_dashboard, name="finance_dashboard"),
    path("admin/withdrawals/", admin_withdrawals, name="admin_withdrawals"),

    # Pages légales / contrat
    path("legal/", affiliate_legal, name="affiliate_legal"),
    path("legal/pdf/", affiliate_legal_pdf, name="affiliate_legal_pdf"),
]
