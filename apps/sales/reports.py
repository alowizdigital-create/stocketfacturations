"""Rapport de marge : ce que les ventes ont réellement rapporté, produit par
produit, sur une période.

Basé sur les ventes confirmées (comme le chiffre d'affaires du tableau de
bord) et sur le coût d'achat figé sur chaque ligne au moment de la vente
(SaleLine.unit_cost). Tout est en HT : la TVA collectée n'est pas un gain.

Les lignes dont le coût est inconnu (produit sans prix d'achat, ligne libre,
vente antérieure à la fonctionnalité) ne sont PAS comptées à coût zéro — ce
serait annoncer une marge de 100 % — mais sorties du calcul et signalées à
part (`revenue_unknown_cost`)."""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce

from django.utils.translation import gettext as _

from apps.cashier import services as cashier_services
from apps.purchasing.models import Expense

from .models import Sale, SaleLine

ZERO = Decimal("0")
_COST = ExpressionWrapper(F("quantity") * F("unit_cost"), output_field=DecimalField(max_digits=20, decimal_places=3))


def _percent(part, whole):
    if not whole:
        return None
    return (part * 100 / whole).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def margin_report(boutique, date_from, date_to):
    """Renvoie {"rows": [...], "totals": {...}} pour les ventes confirmées de
    `boutique` dont la date est dans [date_from, date_to] (bornes incluses).
    Chaque ligne : product_id, name, quantity, revenue (HT), revenue_known
    (part dont le coût est connu), cost, margin, margin_percent (None si le
    coût est inconnu partout), revenue_unknown_cost."""
    known = Q(unit_cost__isnull=False)
    lines = SaleLine.objects.filter(
        sale__boutique=boutique, sale__status=Sale.CONFIRMEE,
        sale__sale_date__gte=date_from, sale__sale_date__lte=date_to,
    )
    grouped = (
        lines.annotate(label=Coalesce("product__name", "description"))
        .values("product_id", "label")
        .annotate(
            total_quantity=Sum("quantity"),
            revenue=Sum("line_total_ht"),
            revenue_known=Sum("line_total_ht", filter=known),
            cost=Sum(_COST, filter=known),
        )
    )

    rows = []
    for item in grouped:
        revenue = item["revenue"] or ZERO
        revenue_known = item["revenue_known"] or ZERO
        cost = (item["cost"] or ZERO).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        has_cost = revenue_known > 0 or cost > 0
        margin = revenue_known - cost
        rows.append({
            "product_id": item["product_id"],
            "name": item["label"],
            "quantity": item["total_quantity"] or ZERO,
            "revenue": revenue,
            "revenue_known": revenue_known,
            "cost": cost,
            "margin": margin,
            "margin_percent": _percent(margin, revenue_known) if has_cost else None,
            "revenue_unknown_cost": revenue - revenue_known,
        })
    # Plus grosse marge d'abord ; les produits au coût inconnu en fin de liste.
    rows.sort(key=lambda r: (r["margin_percent"] is None, -r["margin"], r["name"].lower()))

    revenue = sum((r["revenue"] for r in rows), ZERO)
    revenue_known = sum((r["revenue_known"] for r in rows), ZERO)
    cost = sum((r["cost"] for r in rows), ZERO)
    margin = revenue_known - cost
    # Dépenses de fonctionnement de la même période (hors annulées) : elles
    # s'ajoutent au coût des marchandises pour donner le bénéfice net. Les
    # achats de marchandises n'y sont pas — déjà dans le coût des ventes.
    expense_rows = list(
        Expense.objects.filter(
            boutique=boutique, cancelled_at__isnull=True, expense_date__gte=date_from, expense_date__lte=date_to,
        ).values("category").annotate(total=Sum("amount")).order_by("-total")
    )
    labels = dict(Expense.CATEGORY_CHOICES)
    expenses_by_category = [(labels[r["category"]], r["total"]) for r in expense_rows]
    # Frais de livraison des commandes livrées : une dépense elle aussi, même
    # si elle n'est pas saisie à la main (prélevée dans la caisse du livreur).
    delivery_total, delivery_count = cashier_services.delivery_fees_between(boutique, date_from, date_to)
    if delivery_total > 0:
        expenses_by_category.append((_("Frais de livraison"), delivery_total))
        expenses_by_category.sort(key=lambda item: -item[1])
    expenses_total = sum((r["total"] for r in expense_rows), ZERO) + delivery_total
    totals = {
        "expenses_total": expenses_total,
        "delivery_fees": delivery_total,
        "delivery_count": delivery_count,
        "net_profit": margin - expenses_total,
        "revenue": revenue,
        "revenue_known": revenue_known,
        "cost": cost,
        "margin": margin,
        "margin_percent": _percent(margin, revenue_known),
        "revenue_unknown_cost": revenue - revenue_known,
        "nb_products": len(rows),
    }
    return {"rows": rows, "totals": totals, "expenses_by_category": expenses_by_category}
