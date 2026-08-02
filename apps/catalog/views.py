from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import ProtectedError, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.permissions import boutique_role_required
from apps.tenants.models import Membership

from .forms import CategoryForm, ProductForm, UnitForm
from .models import Category, Product, Unit
from .services import get_effective_price

MANAGE_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE)


@login_required
def product_search(request):
    """Recherche produit en direct pour l'écran de vente (POS) : renvoie
    au fil de la frappe les produits actifs dont le nom, la référence ou le
    code-barres correspond, avec prix effectif et stock de la boutique
    courante."""
    from apps.stock.models import StockLevel

    query = request.GET.get("q", "").strip()
    products = Product.objects.filter(compte=request.compte, is_active=True).select_related("unit")
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode__icontains=query)
        )
    products = products.order_by("name")[:20]

    stock_by_product = {
        level.product_id: level.quantity
        for level in StockLevel.objects.filter(
            boutique=request.boutique, product__in=products
        )
    }

    results = [
        {
            "id": str(product.id),
            "name": product.name,
            "sku": product.sku,
            "unit": str(product.unit),
            "price": float(get_effective_price(product, request.boutique)),
            "tva_rate": float(product.tva_rate),
            "image_url": product.image.url if product.image else None,
            "stock_qty": float(stock_by_product.get(product.id, 0)),
        }
        for product in products
    ]
    return JsonResponse({"results": results})


@login_required
def product_list(request):
    products = (
        Product.objects.filter(compte=request.compte)
        .select_related("category", "unit")
        .order_by("name")
    )
    return render(request, "catalog/product_list.html", {"products": products})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def product_create(request):
    if not Unit.objects.filter(compte=request.compte).exists():
        messages.warning(
            request, "Créez d'abord au moins une unité de mesure avant d'ajouter un produit."
        )
        return redirect("catalog:unit_create")

    if request.method == "POST":
        form = ProductForm(request.POST, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(request, "Produit créé.")
            return redirect("catalog:product_list")
    else:
        form = ProductForm(compte=request.compte)
    return render(request, "catalog/product_form.html", {"form": form, "title": "Nouveau produit"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def product_update(request, product_id):
    product = get_object_or_404(Product, id=product_id, compte=request.compte)
    if request.method == "POST":
        form = ProductForm(request.POST, instance=product, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(request, "Produit modifié.")
            return redirect("catalog:product_list")
    else:
        form = ProductForm(instance=product, compte=request.compte)
    return render(request, "catalog/product_form.html", {"form": form, "title": "Modifier le produit"})


@login_required
def category_list(request):
    categories = Category.objects.filter(compte=request.compte).select_related("parent").order_by("name")
    return render(request, "catalog/category_list.html", {"categories": categories})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def category_create(request):
    if request.method == "POST":
        form = CategoryForm(request.POST, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(request, "Catégorie créée.")
            return redirect("catalog:category_list")
    else:
        form = CategoryForm(compte=request.compte)
    return render(request, "catalog/category_form.html", {"form": form, "title": "Nouvelle catégorie"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def category_update(request, category_id):
    category = get_object_or_404(Category, id=category_id, compte=request.compte)
    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(request, "Catégorie modifiée.")
            return redirect("catalog:category_list")
    else:
        form = CategoryForm(instance=category, compte=request.compte)
    return render(request, "catalog/category_form.html", {"form": form, "title": "Modifier la catégorie"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def category_delete(request, category_id):
    category = get_object_or_404(Category, id=category_id, compte=request.compte)
    if request.method == "POST":
        try:
            category.delete()
            messages.success(request, "Catégorie supprimée.")
        except ProtectedError:
            messages.error(request, "Impossible de supprimer : des produits utilisent encore cette catégorie.")
    return redirect("catalog:category_list")


@login_required
def unit_list(request):
    units = Unit.objects.filter(compte=request.compte).order_by("name")
    return render(request, "catalog/unit_list.html", {"units": units})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def unit_create(request):
    if request.method == "POST":
        form = UnitForm(request.POST, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(request, "Unité créée.")
            return redirect("catalog:unit_list")
    else:
        form = UnitForm(compte=request.compte)
    return render(request, "catalog/unit_form.html", {"form": form, "title": "Nouvelle unité"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def unit_update(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id, compte=request.compte)
    if request.method == "POST":
        form = UnitForm(request.POST, instance=unit, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(request, "Unité modifiée.")
            return redirect("catalog:unit_list")
    else:
        form = UnitForm(instance=unit, compte=request.compte)
    return render(request, "catalog/unit_form.html", {"form": form, "title": "Modifier l'unité"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def unit_delete(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id, compte=request.compte)
    if request.method == "POST":
        try:
            unit.delete()
            messages.success(request, "Unité supprimée.")
        except ProtectedError:
            messages.error(request, "Impossible de supprimer : des produits utilisent encore cette unité.")
    return redirect("catalog:unit_list")
