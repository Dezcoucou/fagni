# fagni/urls.py

from django.contrib import admin
from django.urls import path, include

from django.conf import settings
from django.conf.urls.static import static

# 🔗 On importe la vue orders_list pour garder la compatibilité
from orders.views import orders_list

urlpatterns = [
    # ✅ Accès à l’administration Django
    path('admin/', admin.site.urls),

    # 🏠 Alias "home" pour la page d’accueil
    path('', orders_list, name='home'),

    # ✅ Alias global pour les anciens templates (ancienne structure)
    # Exemple : {% url 'list' %} ou /orders/
    path('orders/', orders_list, name='list'),

    # ✅ Inclusion des vraies routes de l’application "orders"
    # Cela garde toutes les routes existantes dans ton app orders (namespace "orders")
    path('orders/', include(('orders.urls', 'orders'), namespace='orders')),

    # 🔹 Dashboard financier MLM / FAGNI
    path('mlm/', include(('mlm.urls', 'mlm'), namespace='mlm')),
]

# Serving media in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
