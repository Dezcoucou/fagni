from django.urls import path
from crowd import api, views

urlpatterns = [
    # ── FRONTEND COTRANSPORTEUR ─────────────────────────
    path('login/', views.crowd_login, name='crowd-login'),
    path('dashboard/', views.crowd_dashboard, name='crowd-dashboard'),
    path('my-missions/', views.crowd_missions, name='crowd-missions-page'),
    path('logout/', views.crowd_logout, name='crowd-logout'),

    path('offers/', api.api_crowd_offers, name='crowd-offers'),
    path('offers/<int:leg_id>/accept/', api.api_crowd_accept_offer, name='crowd-accept-offer'),
    path('offers/<int:leg_id>/reject/', api.api_crowd_reject_offer, name='crowd-reject-offer'),
    path('missions/', api.api_crowd_missions, name='crowd-missions'),
    path('fcm-token/', api.api_crowd_save_fcm_token, name='crowd-fcm-token'),
]
