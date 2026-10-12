from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def money(value):
    """Montant lisible : 15000 -> « 15 000 » (espace insécable, pas de
    décimales — les montants de la boutique sont des entiers). Une valeur non
    numérique est renvoyée telle quelle."""
    try:
        amount = int(Decimal(str(value)).quantize(Decimal("1")))
    except (InvalidOperation, ValueError, TypeError):
        return value
    return f"{amount:,}".replace(",", " ")
