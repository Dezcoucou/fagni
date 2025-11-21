from django.http import JsonResponse
from .models import Customer

def client_lookup(request):
    phone = request.GET.get("phone", "").strip()

    if not phone:
        return JsonResponse({"exists": False})

    try:
        client = Customer.objects.get(phone=phone)
        return JsonResponse({
            "exists": True,
            "name": client.name,
            "address": client.address or "",
        })
    except Customer.DoesNotExist:
        return JsonResponse({"exists": False})
