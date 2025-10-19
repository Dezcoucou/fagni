from django.urls import path
from . import views_top

app_name = "dashboard"

urlpatterns = [
    path('analytics/', views_top.analytics_home, name='analytics'),
    path('analytics/top-clients/', views_top.dashboard_top_clients, name='dashboard_top_clients'),
    path('analytics/export/csv/',   views_top.export_top_clients_csv, name='export_top_clients_csv'),
    path('analytics/export/xlsx/',  views_top.export_top_clients_xlsx, name='export_top_clients_xlsx'),
]
