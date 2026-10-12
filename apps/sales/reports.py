"""Rapport de marge : ce que les ventes ont réellement rapporté, produit par
produit, sur une période.

Basé sur les ventes confirmées (comme le chiffre d'affaires du tableau de
bord) et sur le coût d'achat figé sur chaque ligne au moment de la vente
(SaleLine.unit_cost). Tout est en HT : la TVA collectée n'est pas un gain.

Les lignes dont le coût est inconnu (produit sans prix d'achat, ligne libre,
vente antérieure à la fonctionnalité) ne sont PAS comptées à coût zéro — ce
serait annoncer une marge de 100 % — mais sorties du calcul et signalées à
part (`revenue_unknown_cost`)."""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce, TruncMonth

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
    sales_count = Sale.objects.filter(
        boutique=boutique, status=Sale.CONFIRMEE, sale_date__gte=date_from, sale_date__lte=date_to,
    ).count()
    totals = {
        "sales_count": sales_count,
        "average_basket": (revenue / sales_count).quantize(Decimal("1"), rounding=ROUND_HALF_UP) if sales_count else None,
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


def previous_period(date_from, date_to):
    """Période de même durée, juste avant [date_from, date_to]."""
    days = (date_to - date_from).days + 1
    prev_to = date_from - timedelta(days=1)
    return prev_to - timedelta(days=days - 1), prev_to


def _delta(current, previous):
    """Variation en % de `current` par rapport à `previous` (None si la base
    de comparaison est nulle : un pourcentage n'aurait pas de sens)."""
    if not previous:
        return None
    current, previous = Decimal(current), Decimal(previous)
    return ((current - previous) * 100 / abs(previous)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


COMPARED_KEYS = ("revenue", "margin", "expenses_total", "net_profit", "sales_count")


def compare_totals(totals, previous_totals):
    """{clé: variation en %} entre deux jeux de totaux de margin_report."""
    return {key: _delta(totals[key], previous_totals[key]) for key in COMPARED_KEYS}


# Au-delà, un point par jour devient illisible : on regroupe par mois.
MAX_DAILY_POINTS = 62


def trend(boutique, date_from, date_to):
    """Évolution des ventes (HT) et de la marge sur la période : un point par
    jour (ou par mois si la période dépasse MAX_DAILY_POINTS jours), les
    périodes sans vente comprises pour que l'axe reste régulier. Même base
    que margin_report (ventes confirmées, coût connu uniquement pour la
    marge). Renvoie {"granularity": "day"|"month", "points": [{label, revenue,
    margin}], "max": plus grande ventes}."""
    monthly = (date_to - date_from).days + 1 > MAX_DAILY_POINTS
    bucket = TruncMonth("sale__sale_date") if monthly else F("sale__sale_date")
    known = Q(unit_cost__isnull=False)
    grouped = (
        SaleLine.objects.filter(
            sale__boutique=boutique, sale__status=Sale.CONFIRMEE,
            sale__sale_date__gte=date_from, sale__sale_date__lte=date_to,
        )
        .annotate(bucket=bucket)
        .values("bucket")
        .annotate(revenue=Sum("line_total_ht"), revenue_known=Sum("line_total_ht", filter=known),
                  cost=Sum(_COST, filter=known))
    )
    by_bucket = {}
    for item in grouped:
        key = item["bucket"]
        key = key.date() if hasattr(key, "date") else key
        if monthly:
            key = key.replace(day=1)
        margin = (item["revenue_known"] or ZERO) - (item["cost"] or ZERO)
        by_bucket[key] = (item["revenue"] or ZERO, margin.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    points = []
    if monthly:
        cursor = date_from.replace(day=1)
        while cursor <= date_to:
            revenue, margin = by_bucket.get(cursor, (ZERO, ZERO))
            points.append({"label": f"{cursor:%m/%Y}", "revenue": revenue, "margin": margin})
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    else:
        cursor = date_from
        while cursor <= date_to:
            revenue, margin = by_bucket.get(cursor, (ZERO, ZERO))
            points.append({"label": f"{cursor:%d/%m}", "revenue": revenue, "margin": margin})
            cursor += timedelta(days=1)
    peak = max((p["revenue"] for p in points), default=ZERO)
    for p in points:
        p["height"] = int(p["revenue"] * 100 / peak) if peak > 0 else 0
    return {"granularity": "month" if monthly else "day", "points": points, "max": peak}
