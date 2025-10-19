from django.urls import path
from orders.admin_site import export_csv

app_name = "fagni_admin"
urlpatterns = [
    path("export_csv/", export_csv, name="export_csv"),
]
