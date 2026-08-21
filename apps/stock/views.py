from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.catalog.models import Product
from apps.catalog.services import get_effective_low_stock_threshold
from apps.core.permissions import boutique_role_required
from apps.sync import outbox
from apps.sync.models import OutboxEntry
from apps.tenants.models import Membership

from . import services
from .forms import ProductQuickMovementForm, StockMovementForm
from .models import StockLevel, StockMovement

MANAGE_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE)


@login_required
def stock_level_list(request):
    query = request.GET.get("q", "").strip()
    # low_stock=1 : lien direct depuis la carte "Produits en stock bas" du
    # tableau de bord (apps.core.views.home) — le seuil étant calculé en
    # Python par produit (pas un simple filtre SQL), il faut évaluer tous
    # les niveaux avant de filtrer, donc pas de troncature à 10 ici.
    low_stock_only = request.GET.get("low_stock") == "1"
    levels = StockLevel.objects.filter(boutique=request.boutique).select_related("product")
    if query:
        levels = levels.filter(Q(product__name__icontains=query) | Q(product__sku__icontains=query))
    levels = levels.order_by("product__name")
    if not query and not low_stock_only:
        levels = levels[:10]
    rows = []
    for level in levels:
        threshold = get_effective_low_stock_threshold(level.product, request.boutique)
        is_low = level.quantity <= threshold
        if low_stock_only and not is_low:
            continue
        rows.append({"level": level, "threshold": threshold, "is_low": is_low})
    return render(
        request, "stock/stock_level_list.html",
        {"rows": rows, "query": query, "low_stock_only": low_stock_only},
    )


@login_required
@boutique_role_required(*MANAGE_ROLES)
def movement_create(request):
    if request.method == "POST":
        form = StockMovementForm(request.POST, compte=request.compte)
        if form.is_valid():
            movement = services.apply_movement(
                boutique=request.boutique,
                product=form.cleaned_data["product"],
                type=form.cleaned_data["type"],
                quantity=form.signed_quantity(),
                reason=form.cleaned_data["reason"],
                created_by=request.user,
            )
            if settings.IS_OFFLINE:
                outbox.enqueue(OutboxEntry.STOCK_MOVEMENT, movement.id)
            messages.success(request, _("Mouvement de stock enregistré."))
            return redirect("stock:stock_level_list")
    else:
        form = StockMovementForm(compte=request.compte)
    return render(request, "stock/movement_form.html", {"form": form})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def product_quick_movement(request, product_id):
    """Mouvement compléter/retirer depuis la fiche produit — deux boutons
    soumettent le même formulaire avec un `type` différent (ENTREE/SORTIE),
    pas de champ produit à sélectionner puisqu'il vient de l'URL."""
    product = get_object_or_404(Product, id=product_id, compte=request.compte)
    movement_type = request.POST.get("type")
    if movement_type not in (StockMovement.ENTREE, StockMovement.SORTIE):
        messages.error(request, _("Type de mouvement invalide."))
        return redirect("catalog:product_detail", product_id=product.id)

    form = ProductQuickMovementForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Quantité invalide."))
        return redirect("catalog:product_detail", product_id=product.id)

    quantity = form.cleaned_data["quantity"]
    signed_quantity = -quantity if movement_type == StockMovement.SORTIE else quantity
    movement = services.apply_movement(
        boutique=request.boutique,
        product=product,
        type=movement_type,
        quantity=signed_quantity,
        reason=form.cleaned_data["reason"],
        created_by=request.user,
    )
    if settings.IS_OFFLINE:
        outbox.enqueue(OutboxEntry.STOCK_MOVEMENT, movement.id)
    messages.success(request, "Mouvement de stock enregistré.")
    return redirect("catalog:product_detail", product_id=product.id)


@login_required
def movement_history(request):
    query = request.GET.get("q", "").strip()
    movements = StockMovement.objects.filter(boutique=request.boutique).select_related("product")
    if query:
        movements = movements.filter(Q(product__name__icontains=query) | Q(reason__icontains=query))
    movements = movements.order_by("-created_at")
    movements = movements[:10] if not query else movements[:200]
    return render(request, "stock/movement_history.html", {"movements": movements, "query": query})
