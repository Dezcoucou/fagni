from .admin_dashboard import DashboardAdminSite
from django.contrib import admin

admin.site = DashboardAdminSite(name='fagni_admin')
