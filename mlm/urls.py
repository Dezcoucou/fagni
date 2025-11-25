from django.urls import path

from .views import (
    affiliate_dashboard,
    affiliate_withdrawals,
    affiliate_withdrawal_request,
    admin_mlm_dashboard,
    admin_withdrawals,
    finance_dashboard,
    affiliate_legal,
    affiliate_legal_pdf,
)

app_name = "mlm"

urlpatterns = [
    path("affiliate/", affiliate_dashboard, name="affiliate_dashboard"),
    path(
        "affiliate/withdrawals/",
        affiliate_withdrawals,
        name="affiliate_withdrawals",
    ),
    path(
        "affiliate/withdrawals/request/",
        affiliate_withdrawal_request,
        name="affiliate_withdrawal_request",
    ),
    path("admin-dashboard/", admin_mlm_dashboard, name="admin_dashboard"),
    path("admin-withdrawals/", admin_withdrawals, name="admin_withdrawals"),
    path("finance/", finance_dashboard, name="finance_dashboard"),
    path("legal/", affiliate_legal, name="affiliate_legal"),
    path("legal/pdf/", affiliate_legal_pdf, name="affiliate_legal_pdf"),
]
