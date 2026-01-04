from django import template
from .money_filters import fcfa as _fcfa

register = template.Library()

@register.filter
def fcfa(value):
    return _fcfa(value)
