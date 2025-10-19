from decimal import Decimal, ROUND_HALF_UP
from django import template
from decimal import Decimal
register = template.Library()

@register.filter
def fcfa(amount):
    if amount in (None, ""): return "-"
    try:
        q = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        q = Decimal("0.00")
    # Pas d’espace insécable pour simplicité mobile
    return f"{q} FCFA"
