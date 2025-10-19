from django.shortcuts import render

def order_thanks(request):
    return render(request, 'order_thanks.html', {'message': 'Merci pour votre commande !'})

