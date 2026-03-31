from decimal import Decimal

from django.db import transaction

from pricing.models import PricingRule, PriceQuote


@transaction.atomic
def create_estimated_quote(*, order, notes=""):
    logistics_fee = Decimal("0")
    service_fee = Decimal("0")
    subtotal_amount = Decimal("0")
    discount_amount = Decimal("0")

    # Exemple simple de logique initiale :
    # - si une règle fixed_fee existe sur collecte_livraison, on l'utilise
    # - sinon zéro
    fixed_rule = (
        PricingRule.objects.filter(
            rule_type="fixed_fee",
            is_active=True,
        )
        .order_by("priority", "id")
        .first()
    )
    if fixed_rule:
        logistics_fee = Decimal(str(fixed_rule.value))

    total_amount = subtotal_amount + logistics_fee + service_fee - discount_amount

    quote = PriceQuote.objects.create(
        order=order,
        quote_type="estimated",
        subtotal_amount=subtotal_amount,
        logistics_fee=logistics_fee,
        service_fee=service_fee,
        discount_amount=discount_amount,
        total_amount=total_amount,
        currency="XOF",
        is_final=False,
        notes=notes or "Devis estimatif initial V2",
    )
    return quote
