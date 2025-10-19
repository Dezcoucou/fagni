from django.shortcuts import render
from .forms import SingleImageForm
def upload(request):
    if request.method == "POST":
        form = SingleImageForm(request.POST, request.FILES)
        form.is_valid()
    else:
        form = SingleImageForm()
    return render(request, "api/upload.html", {"form": form})

from django.http import HttpResponse

def order_thanks(request):
    return HttpResponse("Commande enregistrée. Merci !")
