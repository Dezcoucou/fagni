from django import forms
from .models import Order

class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["code", "client", "phone", "item", "unit_price", "quantity", "status"]
        widgets = {
            "status": forms.Select(),
        }
