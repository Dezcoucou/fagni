# fagni/urls.py

from django.contrib import admin
from django.urls import path, include

# 🔗 On importe la vue orders_list pour garder la compatibilité
from orders.views import orders_list

urlpatterns = [
    # ✅ Accès à l’administration Django
    path('admin/', admin.site.urls),

    # ✅ Alias global pour les anciens templates (ancienne structure)
    # Exemple : {% url 'list' %} ou /orders/
    path('orders/', orders_list, name='list'),

    # ✅ Inclusion des vraies routes de l’application "orders"
    # Cela garde toutes les routes existantes dans ton app orders (namespace "orders")
    path('orders/', include(('orders.urls', 'orders'), namespace='orders')),
]

# 🛟 Bloc de secours (fallback)
# Permet d’éviter les erreurs 500 si certaines URLs sont manquantes
try:
    urlpatterns += [path('', include('fagni.urls_fallback'))]
except Exception:
    # Si le fichier urls_fallback.py n’existe pas encore, on ignore sans bloquer
    pass