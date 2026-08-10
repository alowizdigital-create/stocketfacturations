from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.catalog.services import get_effective_low_stock_threshold
from apps.core.permissions import boutique_role_required
from apps.sync import outbox
from apps.sync.models import OutboxEntry
from apps.tenants.models import Membership

from . import services
from .forms import StockMovementForm
from .models import StockLevel, StockMovement

MANAGE_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE)


@login_required
def stock_level_list(request):
    levels = (
        StockLevel.objects.filter(boutique=request.boutique)
        .select_related("product")
        .order_by("product__name")
    )
    rows = []
    for level in levels:
        threshold = get_effective_low_stock_threshold(level.product, request.boutique)
        rows.append({"level": level, "threshold": threshold, "is_low": level.quantity <= threshold})
    return render(request, "stock/stock_level_list.html", {"rows": rows})


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
            messages.success(request, "Mouvement de stock enregistré.")
            return redirect("stock:stock_level_list")
    else:
        form = StockMovementForm(compte=request.compte)
    return render(request, "stock/movement_form.html", {"form": form})


@login_required
def movement_history(request):
    movements = (
        StockMovement.objects.filter(boutique=request.boutique)
        .select_related("product")
        .order_by("-created_at")[:200]
    )
    return render(request, "stock/movement_history.html", {"movements": movements})
