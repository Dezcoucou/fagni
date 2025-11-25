from decimal import Decimal

from django import forms
from django.db.models import Sum

from .models import WithdrawalRequest, WalletTransaction, ReferralLink


class WithdrawalRequestForm(forms.ModelForm):
    """
    Formulaire de demande de retrait côté affilié.
    On contrôle :
    - montant > 0
    - montant >= minimum (ex: 5 000 FCFA)
    - montant <= solde disponible
    """

    class Meta:
        model = WithdrawalRequest
        fields = ["amount", "payout_method"]
        labels = {
            "amount": "Montant à retirer (FCFA)",
            "payout_method": "Moyen de paiement souhaité",
        }
        help_texts = {
            "payout_method": "Ex : Orange Money, MTN, Wave, compte bancaire…",
        }

    def __init__(self, *args, profile: ReferralLink | None = None, **kwargs):
        self.profile = profile
        super().__init__(*args, **kwargs)

    def clean_amount(self):
        amount = self.cleaned_data.get("amount") or 0
        if amount <= 0:
            raise forms.ValidationError("Le montant doit être supérieur à 0 FCFA.")

        # 🔹 Solde actuel du wallet affilié
        agg = WalletTransaction.objects.filter(
            profile=self.profile,
        ).aggregate(total=Sum("amount"))
        balance = int(agg["total"] or 0)

        # 🔹 Minimum de retrait (valeur fixe pour l’instant)
        min_amount = 5000

        if amount < min_amount:
            raise forms.ValidationError(
                f"Le montant minimum de retrait est de {min_amount} FCFA."
            )

        if amount > balance:
            raise forms.ValidationError(
                "Le montant demandé est supérieur à votre solde disponible."
            )

        return amount
