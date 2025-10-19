from django.http import HttpResponseRedirect
from django.urls import path, reverse

def redirect_safe(request, name):
    try:
        return HttpResponseRedirect(reverse(name))
    except Exception:
        return HttpResponseRedirect('/')

urlpatterns = [
    path('accueil/', lambda r: redirect_safe(r, 'accueil')),
    path('index/', lambda r: redirect_safe(r, 'admin:index')),
    path('commander/', lambda r: redirect_safe(r, 'commander')),
    path('contact/', lambda r: redirect_safe(r, 'contact')),
    path('create_multi/', lambda r: redirect_safe(r, 'create_multi')),
    path('export_orders_csv/', lambda r: redirect_safe(r, 'export_orders_csv')),
    path('export_orders_xlsx/', lambda r: redirect_safe(r, 'export_orders_xlsx')),
    path('home/', lambda r: redirect_safe(r, 'home')),
    path('order/', lambda r: redirect_safe(r, 'order')),
    path('order_detail/', lambda r: redirect_safe(r, 'order_detail')),
    path('order_item_photo_delete/', lambda r: redirect_safe(r, 'order_item_photo_delete')),
    path('order_list/', lambda r: redirect_safe(r, 'order_list')),
    path('create/', lambda r: redirect_safe(r, 'orders:create')),
    path('delete/', lambda r: redirect_safe(r, 'orders:delete')),
    path('detail/', lambda r: redirect_safe(r, 'orders:detail')),
    path('export_top_clients_csv/', lambda r: redirect_safe(r, 'orders:export_top_clients_csv')),
    path('export_top_clients_xlsx/', lambda r: redirect_safe(r, 'orders:export_top_clients_xlsx')),
    path('list/', lambda r: redirect_safe(r, 'orders:list')),
    path('orders_list/', lambda r: redirect_safe(r, 'orders:orders_list')),
    path('update/', lambda r: redirect_safe(r, 'orders:update')),
]