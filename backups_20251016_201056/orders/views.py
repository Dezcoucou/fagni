from django.shortcuts import render, redirect, get_object_or_404
from .models import Order
from .forms import OrderForm

def orders_list(request):
    orders = Order.objects.order_by("-created_at")
    return render(request, "orders/orders_list.html", {"orders": orders})

def order_create(request):
    if request.method == "POST":
        form = OrderForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("orders:success")
    else:
        form = OrderForm()
    return render(request, "orders/order_new.html", {"form": form})

def order_success(request):
    return render(request, "orders/order_success.html")

def order_detail(request, pk):
    order = get_object_or_404(Order, pk=pk)
    return render(request, "orders/detail.html", {"order": order})
