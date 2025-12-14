from django import template

register = template.Library()

@register.filter
def money(value):
    """
    Format numérique pour affichage :
    - 1 000
    - 25 750
    - 1 245 900
    Sans décimales.
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value

    # Format avec séparateur espace
    formatted = f"{value:,.0f}".replace(",", " ")
    return formatted
