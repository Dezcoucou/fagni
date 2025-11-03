from django.http import JsonResponse, HttpResponse

def home(request):
    return HttpResponse("Bienvenue sur la plateforme FAGNI – tout fonctionne !")

def ping_public(request):
    return JsonResponse({"status": "ok", "scope": "public"})

def ping_commandes(request):
    return JsonResponse({"status": "ok", "scope": "commandes"})

def ping_clients(request):
    return JsonResponse({"status": "ok", "scope": "clients"})
