from django.db import transaction
from django.db.models import F

from .models import StockLevel, StockMovement


@transaction.atomic
def apply_movement(*, boutique, product, type, quantity, created_by=None, reason="", unit_cost=None,
                    reference_invoice=None, reference_sale=None, source=StockMovement.SOURCE_ONLINE,
                    movement_id=None, created_at=None):
    """Point d'entrée unique pour toute écriture de stock : crée la ligne
    de registre (immuable) puis met à jour le cache StockLevel dans la
    même transaction. `movement_id`/`created_at` permettent de rejouer un
    mouvement créé hors-ligne avec son UUID et son horodatage d'origine."""

    kwargs = dict(
        boutique=boutique,
        product=product,
        type=type,
        quantity=quantity,
        unit_cost=unit_cost,
        reason=reason,
        reference_invoice=reference_invoice,
        reference_sale=reference_sale,
        created_by=created_by,
        source=source,
    )
    if movement_id is not None:
        kwargs["id"] = movement_id
    if created_at is not None:
        kwargs["created_at"] = created_at

    movement = StockMovement.objects.create(**kwargs)

    level, _ = StockLevel.objects.get_or_create(boutique=boutique, product=product)
    StockLevel.objects.filter(pk=level.pk).update(quantity=F("quantity") + quantity)

    return movement


def recompute_stock_level(boutique, product):
    """Filet de sécurité : recalcule un StockLevel à partir du registre
    complet, au cas où le cache aurait dérivé."""
    from django.db.models import Sum

    total = StockMovement.objects.filter(boutique=boutique, product=product).aggregate(
        total=Sum("quantity")
    )["total"] or 0
    level, _ = StockLevel.objects.get_or_create(boutique=boutique, product=product)
    StockLevel.objects.filter(pk=level.pk).update(quantity=total)
    return total
