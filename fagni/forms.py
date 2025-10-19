from django import forms
from .models import Commande

class CommandeForm(forms.ModelForm):
    class Meta:
        model = Commande
        fields = [
            "nom_client",
            "telephone",
            "adresse_livraison",
            "type_service",
            "zone",
            "partenaire",
            "instructions",
        ]
        widgets = {
            "nom_client": forms.TextInput(attrs={"placeholder": "Votre nom complet"}),
            "telephone": forms.TextInput(attrs={"placeholder": "Votre numéro de téléphone"}),
            "adresse_livraison": forms.Textarea(attrs={"rows": 3, "placeholder": "Rue, quartier, indications…"}),
            "type_service": forms.Select(),
            "zone": forms.TextInput(attrs={"placeholder": "Quartier / commune"}),
            "partenaire": forms.TextInput(attrs={"placeholder": "Laisse vide pour auto-assignation"}),
            "instructions": forms.Textarea(attrs={"rows": 3, "placeholder": "Ex. préférence d’horaire, consignes…"}),
        }