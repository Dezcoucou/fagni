from django.urls import path
from crowd import api

urlpatterns = [
    path('offers/', api.api_crowd_offers, name='crowd-offers'),
    path('offers/<int:leg_id>/accept/', api.api_crowd_accept_offer, name='crowd-accept-offer'),
    path('offers/<int:leg_id>/reject/', api.api_crowd_reject_offer, name='crowd-reject-offer'),
    path('missions/', api.api_crowd_missions, name='crowd-missions'),
]
