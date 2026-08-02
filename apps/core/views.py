from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import redirect, render
from django.utils import timezone

from apps.catalog.models import Product
from apps.catalog.services import get_effective_low_stock_threshold
from apps.sales.models import Invoice, Sale
from apps.stock.models import StockLevel


@login_required
def home(request):
    if request.boutique is None:
        return redirect("tenants:choose_boutique")

    boutique = request.boutique
    today = timezone.localdate()
    month_start = today.replace(day=1)

    # Le chiffre d'affaires reflète les ventes confirmées, pas seulement
    # celles pour lesquelles une facture a été générée — beaucoup de
    # ventes n'en ont jamais besoin.
    ca_mois = (
        Sale.objects.filter(
            boutique=boutique,
            status=Sale.CONFIRMEE,
            sale_date__gte=month_start,
        ).aggregate(total=Sum("total_ttc"))["total"]
        or 0
    )

    nb_produits = Product.objects.filter(compte=request.compte, is_active=True).count()

    nb_factures_impayees = Invoice.objects.filter(
        boutique=boutique,
        type=Invoice.FACTURE,
        status__in=[Invoice.VALIDEE, Invoice.PARTIELLEMENT_PAYEE],
    ).count()

    levels = StockLevel.objects.filter(boutique=boutique).select_related("product")
    nb_stock_bas = sum(
        1
        for level in levels
        if level.quantity <= get_effective_low_stock_threshold(level.product, boutique)
    )

    dernieres_ventes = (
        Sale.objects.filter(boutique=boutique).select_related("client").order_by("-created_at")[:5]
    )

    context = {
        "ca_mois": ca_mois,
        "nb_produits": nb_produits,
        "nb_factures_impayees": nb_factures_impayees,
        "nb_stock_bas": nb_stock_bas,
        "dernieres_ventes": dernieres_ventes,
    }
    return render(request, "core/home.html", context)
