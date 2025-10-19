from django.urls import path
from . import views
urlpatterns = [
    path('order/thanks/', views.order_thanks, name='order_thanks'),path('', views.upload, name='upload')]
