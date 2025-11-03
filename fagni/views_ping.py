from django.http import JsonResponse, HttpResponse

def home(request):
    return HttpResponse("OK /", content_type="text/plain")

def ping_public(request):
    return JsonResponse({"status": "ok", "service": "public"})

def ping_commandes(request):
    return JsonResponse({"status": "ok", "service": "commandes"})

def ping_clients(request):
    return JsonResponse({"status": "ok", "service": "clients"})
