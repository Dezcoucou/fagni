from django import forms
from django.forms import inlineformset_factory
from .models import Customer, Order, OrderItem, Payment, Delivery

# === Formulaire client ===
class CustomerForm(forms.ModelForm):
    address = forms.CharField(
        required=True,
        error_messages={
            "required": "Veuillez indiquer votre adresse de collecte pour que le livreur puisse vous trouver."
        },
        widget=forms.TextInput(attrs={
            "placeholder": "Ex: Cocody Riviera 3, près du carrefour Shell"
        }),
    )

    class Meta:
        model = Customer
        fields = ["name", "phone", "email", "address"]

# === Formulaire commande ===
class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        fields = ["customer", "status"]

# === Formulaire ligne de commande ===
class OrderItemForm(forms.ModelForm):
    class Meta:
        model = OrderItem
        fields = ["designation", "quantity", "unit_price"]

# === Formulaire paiement ===
class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["channel", "amount", "reference", "source"]

# === Formulaire livraison ===
class DeliveryForm(forms.ModelForm):
    class Meta:
        model = Delivery
        fields = ["address", "scheduled_at", "status", "notes"]

# === InlineFormSets ===
OrderItemFormSet = inlineformset_factory(
    Order, OrderItem, form=OrderItemForm, extra=1, can_delete=True
)

PaymentFormSet = inlineformset_factory(
    Order, Payment, form=PaymentForm, extra=0, can_delete=True
)
