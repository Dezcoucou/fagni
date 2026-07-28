from orders.partner_api import partner_order_detail, partner_refuse_order, partner_login, partner_orders, partner_update_status
from orders.ops_api import api_ops_pilotbook, api_ops_pilotbook_detail, api_ops_prospects, api_ops_prospect_detail, api_ops_credit_client_wallet, api_ops_all_photos, ops_suggest_pressing, ops_suggest_driver, ops_assign_return_driver, api_wallet_solde, api_partner_penalty, api_partner_bonus, api_partner_score_history, api_wallet_retrait, api_ops_paiements, api_ops_enregistrer_paiement, api_ops_revenus, api_ops_rapport_hebdo, api_ops_activite_jour, api_ops_wallets, api_score_pressing, api_score_livreur, api_creer_parrainage_v2secure, api_stats_parrainage, api_valider_code_parrainage, ops_login, ops_dashboard, ops_assign_partner, ops_update_status, ops_update_litige_status, ops_assign_driver, ops_mark_paid, ops_add_partner, ops_add_driver, ops_list_partners, api_simulateur_notify, api_ops_routine_essais, api_ops_routine_satisfaction, api_ops_routine_proposer_abonnement
from orders.driver_api import save_fcm_token, api_driver_dropoff, driver_login, driver_missions, driver_confirm_pickup, driver_delivery_proof, driver_wallet, driver_toggle_status, driver_pending_mission, driver_copilote, api_driver_profil_update, driver_update_location
from orders.photo_api import driver_upload_photo, partner_upload_photo, order_photos
from orders.config_api import api_config
from orders.client_api import api_chatbot, api_login, api_home, api_orders, api_order_detail, api_pricing_bags, api_create_order, api_articles, api_wallet, api_parrainage, api_rate_order, api_register, api_report_litige, api_pricing_detail, api_order_tracking, api_cancel_order, api_abonnement_estimer, api_abonnement_reserver, api_mon_abonnement_v1, api_routine_essai, api_routine_essai_detail
from django.contrib import admin
from django.urls import path, include
from fagni.views import home, landing_riviera3, api_health
from django.views.generic import RedirectView
from django.contrib.staticfiles.storage import staticfiles_storage


urlpatterns = [
    # ── API CLIENT FAGNI ──────────────────────────────────
    path("api/client/auth/login/",    api_login,    name="api-client-login"),
    path("api/client/auth/register/", api_register, name="api-client-register"),
    path("api/client/chatbot/", api_chatbot, name="chatbot"),
    path("api/client/home/",       api_home,   name="api-client-home"),
    path("api/client/orders/",     api_orders,       name="api-client-orders"),
    path("api/client/orders/<int:order_id>/", api_order_detail, name="api-client-order-detail"),
    path("api/client/pricing/bags/", api_pricing_bags,  name="api-client-pricing-bags"),
    path("api/client/orders/create/",  api_create_order, name="api-client-create-order"),
    path("api/client/articles/",          api_articles,     name="api-client-articles"),
    path("api/client/wallet/",             api_wallet,       name="api-client-wallet"),
    path("api/client/parrainage/",         api_parrainage,   name="api-client-parrainage"),
    path("api/config/", api_config, name="api-config"),
    path("api/health/", api_health, name="api-health"),
    path("api/fcm/token/", save_fcm_token, name="save-fcm-token"),
    path("api/client/orders/<int:order_id>/rate/",   api_rate_order,    name="api-client-rate"),
    path("api/client/orders/<int:order_id>/litige/",    api_report_litige,    name="api-client-litige"),
    path("api/client/orders/<int:order_id>/tracking/", api_order_tracking, name="api-client-tracking"),
    path("api/client/orders/<int:order_id>/cancel/",   api_cancel_order,   name="api-client-cancel"),
    path("api/client/pricing/detail/",               api_pricing_detail, name="api-client-pricing-detail"),
    path("api/partner/login/",              partner_login,         name="api-partner-login"),
    path("api/partner/orders/<int:order_id>/", partner_order_detail),
    path("api/partner/orders/",             partner_orders,        name="api-partner-orders"),
    path("api/partner/orders/<int:order_id>/status/", partner_update_status, name="api-partner-status"),
    path("api/partner/orders/<int:order_id>/refuse/", partner_refuse_order, name="api-partner-refuse"),
    path("api/ops/login/",                        ops_login,          name="api-ops-login"),
    path("api/wallet/solde/", api_wallet_solde, name="wallet-solde"),
    path("api/wallet/retrait/", api_wallet_retrait, name="wallet-retrait"),
    path("api/simulateur/notify/", api_simulateur_notify, name="simulateur-notify"),
    path("api/abonnement/estimer/", api_abonnement_estimer, name="abonnement-estimer"),
    path("api/abonnement/reserver/", api_abonnement_reserver, name="abonnement-reserver"),
    path("api/abonnement/mon-abonnement/", api_mon_abonnement_v1, name="mon-abonnement-v1"),
    path("api/routine/essai/", api_routine_essai, name="routine-essai"),
    path("api/routine/essai/<int:order_id>/", api_routine_essai_detail, name="routine-essai-detail"),
    path("api/ops/routine-essais/", api_ops_routine_essais, name="ops-routine-essais"),
    path("api/ops/routine-essais/<int:order_id>/satisfaction/", api_ops_routine_satisfaction, name="ops-routine-satisfaction"),
    path("api/ops/routine-essais/<int:order_id>/proposer-abonnement/", api_ops_routine_proposer_abonnement, name="ops-routine-proposer"),
    path("api/parrainage/creer/", api_creer_parrainage_v2secure, name="parrainage-creer"),
    path("api/parrainage/valider/", api_valider_code_parrainage, name="parrainage-valider"),
    path("api/parrainage/stats/<str:parrain_type>/<int:parrain_id>/", api_stats_parrainage, name="parrainage-stats"),
    path("api/ops/score/pressing/<int:partner_id>/", api_score_pressing, name="score-pressing"),
    path("api/ops/score/livreur/<int:driver_id>/", api_score_livreur, name="score-livreur"),
    path("api/ops/rapport/hebdo/", api_ops_rapport_hebdo, name="ops-rapport-hebdo"),
    path("api/ops/activite/jour/", api_ops_activite_jour, name="ops-activite-jour"),
    path("api/ops/wallets/", api_ops_wallets, name="ops-wallets"),
    path("api/ops/revenus/", api_ops_revenus, name="ops-revenus"),
    path("api/ops/paiements/enregistrer/", api_ops_enregistrer_paiement, name="ops-paiements-enregistrer"),
    path("api/ops/paiements/", api_ops_paiements, name="ops-paiements"),
    path("api/ops/dashboard/",                    ops_dashboard,      name="api-ops-dashboard"),
    path("api/ops/orders/<int:order_id>/suggest-pressing/", ops_suggest_pressing, name="api-ops-suggest-pressing"),
    path("api/ops/orders/<int:order_id>/suggest-driver/", ops_suggest_driver, name="api-ops-suggest-driver"),
    path("api/ops/orders/<int:order_id>/assign/", ops_assign_partner, name="api-ops-assign"),
    path("api/ops/orders/<int:order_id>/status/", ops_update_status,  name="api-ops-status"),
    path("api/ops/orders/<int:order_id>/litige-status/", ops_update_litige_status, name="api-ops-litige-status"),
    path("api/ops/orders/<int:order_id>/assign-return-driver/", ops_assign_return_driver),
    path("api/ops/orders/<int:order_id>/assign-driver/", ops_assign_driver, name="api-ops-assign-driver"),
    path("api/ops/orders/<int:order_id>/mark-paid/",    ops_mark_paid,    name="api-ops-mark-paid"),
    path("api/ops/partners/",      ops_list_partners, name="api-ops-partners"),
    path("api/ops/prospects/", api_ops_prospects, name="api-ops-prospects"),
    path("api/ops/pilotbook/", api_ops_pilotbook, name="api-ops-pilotbook"),
    path("api/ops/pilotbook/<int:entry_id>/", api_ops_pilotbook_detail, name="api-ops-pilotbook-detail"),
    path("api/ops/prospects/<int:prospect_id>/", api_ops_prospect_detail, name="api-ops-prospect-detail"),
    path("api/ops/wallet/credit-client/", api_ops_credit_client_wallet, name="api-ops-credit-client"),
    path("api/ops/photos/", api_ops_all_photos, name="api-ops-all-photos"),
    path("api/ops/partners/add/",  ops_add_partner,   name="api-ops-add-partner"),
    path("api/ops/partners/<int:partner_id>/penalty/",      api_partner_penalty,       name="api-partner-penalty"),
    path("api/ops/partners/<int:partner_id>/bonus/",        api_partner_bonus,         name="api-partner-bonus"),
    path("api/ops/partners/<int:partner_id>/score-history/",api_partner_score_history, name="api-partner-score-history"),
    path("api/ops/drivers/add/",   ops_add_driver,    name="api-ops-add-driver"),
    path("api/driver/login/",                          driver_login,            name="api-driver-login"),
    path("api/driver/missions/",                       driver_missions,         name="api-driver-missions"),
    path("api/driver/orders/<int:order_id>/dropoff/", api_driver_dropoff, name="driver-dropoff"),
    path("api/driver/orders/<int:order_id>/pickup/",   driver_confirm_pickup,   name="api-driver-pickup"),
    path("api/driver/orders/<int:order_id>/delivery-proof/", driver_delivery_proof, name="api-driver-delivery-proof"),
    path("api/driver/orders/<int:order_id>/photo/",    driver_upload_photo,    name="api-driver-photo"),
    path("api/driver/wallet/",                         driver_wallet,           name="api-driver-wallet"),
    path("api/driver/status/", driver_toggle_status, name="api-driver-status"),
    path("api/driver/pending/", driver_pending_mission, name="api-driver-pending"),
    path("api/driver/copilote/", driver_copilote, name="api-driver-copilote"),
    path("api/driver/location/", driver_update_location, name="api-driver-location"),
    path("api/partner/orders/<int:order_id>/photo/",   partner_upload_photo,   name="api-partner-photo"),
    path("api/ops/orders/<int:order_id>/photos/",      order_photos,           name="api-ops-photos"),

    path("admin/", admin.site.urls),
    path("", home, name="home"),
    path("riviera3/", landing_riviera3),
    path("dashboard/", include("dashboard.urls")),
    path("orders/", include(("orders.urls", "orders"), namespace="orders")),
    path("mlm/", include(("mlm.urls", "mlm"), namespace="mlm")),
    path('favicon.ico', RedirectView.as_view(url='/static/favicon.ico')),
]

# Dev media files
from django.conf import settings
from django.conf.urls.static import static

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)