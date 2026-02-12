from decimal import Decimal, InvalidOperation
from django import template

register = template.Library()


def _to_decimal(value) -> Decimal:
    """
    Convertit en Decimal de façon safe.
    Retourne Decimal('0') si None / invalide.
    """
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _format_int_spaces(value: Decimal) -> str:
    """
    Formate un nombre (arrondi à l'unité) avec séparateur espace:
    1245900 -> '1 245 900'
    """
    # arrondi à l'unité
    try:
        q = value.quantize(Decimal("1"))
    except Exception:
        q = Decimal("0")
    # format avec virgules puis remplace par espaces
    return f"{q:,.0f}".replace(",", " ")


@register.filter
def money(value):
    """
    Compat: format numérique sans décimales avec séparateur espace.
    Ex:
      1000 -> '1 000'
      25750 -> '25 750'
    """
    dec = _to_decimal(value)
    return _format_int_spaces(dec)


@register.filter
def fcfa(value):
    """
    Format FCFA standard:
      11619 -> '11 619 FCFA'
    """
    dec = _to_decimal(value)
    return f"{_format_int_spaces(dec)} FCFA"


@register.filter
def fcfa0(value):
    """
    Format FCFA sans suffixe (juste le nombre):
      11619 -> '11 619'
    """
    dec = _to_decimal(value)
    return _format_int_spaces(dec)


@register.filter
def get_item(d, k):
    try:
        return d.get(k)
    except Exception:
        return None
