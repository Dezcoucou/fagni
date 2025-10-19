from django.shortcuts import render
from django.http import HttpResponse

def order(request):
    return HttpResponse("✅ Page de commande à venir (module en construction).")

def order_thanks(request):
    return HttpResponse("Merci pour votre commande !")
