from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import ProtectedError, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.core.permissions import block_when_offline, boutique_role_required
from apps.stock import services as stock_services
from apps.stock.models import StockMovement
from apps.sync import outbox as sync_outbox
from apps.sync.models import OutboxEntry
from apps.tenants.models import Membership

from .forms import CategoryForm, ProductForm, UnitForm
from .models import Category, Product, ProductImage, Unit
from .services import get_effective_low_stock_threshold, get_effective_price

MANAGE_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE)


@login_required
# def product_search(request):
#     """Recherche produit en direct pour l'écran de vente (POS) : renvoie
#     au fil de la frappe les produits actifs dont le nom, la référence ou le
#     code-barres correspond, avec prix effectif et stock de la boutique
#     courante."""
#     from apps.stock.models import StockLevel

#     query = request.GET.get("q", "").strip()
#     products = Product.objects.filter(compte=request.compte, is_active=True).select_related("unit")
#     if query:
#         products = products.filter(
#             Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode__icontains=query)
#         )
#     products = products.order_by("name")[:20]

#     stock_by_product = {
#         level.product_id: level.quantity
#         for level in StockLevel.objects.filter(
#             boutique=request.boutique, product__in=products
#         )
#     }

#     results = [
#         {
#             "id": str(product.id),
#             "name": product.name,
#             "sku": product.sku,
#             "unit": str(product.unit),
#             "price": float(get_effective_price(product, request.boutique)),
#             "tva_rate": float(product.tva_rate),
#             "image_url": product.image.url if product.image else None,
#             "stock_qty": float(stock_by_product.get(product.id, 0)),
#         }
#         for product in products
#     ]
#     return JsonResponse({"results": results})

@login_required
def product_search(request):
    """Recherche produit en direct pour la vente/le devis : ne renvoie que
    les produits en stock dans la boutique courante — un produit en rupture
    ne peut pas être vendu, donc ne doit pas apparaître dans la liste.
    ?include_out_of_stock=1 lève ce filtre — utilisé par le formulaire de
    mouvement de stock, où un produit à 0 est justement le cas d'usage
    principal d'une entrée (réapprovisionnement)."""
    from apps.stock.models import StockLevel

    query = request.GET.get("q", "").strip()
    include_out_of_stock = request.GET.get("include_out_of_stock") == "1"

    products = Product.objects.filter(compte=request.compte, is_active=True).select_related("unit")
    if not include_out_of_stock:
        products = products.filter(
            stock_levels__boutique=request.boutique,
            stock_levels__quantity__gt=0,
        )

    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(sku__icontains=query) |
            Q(barcode__icontains=query)
        )

    # Trier, limiter puis convertir en liste
    products = list(products.order_by("name")[:3])

    product_ids = [p.id for p in products]

    stock_by_product = {
        level.product_id: level.quantity
        for level in StockLevel.objects.filter(
            boutique=request.boutique,
            product_id__in=product_ids,
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
    from apps.stock.models import StockLevel

    query = request.GET.get("q", "").strip()

    products = Product.objects.filter(compte=request.compte).select_related("category", "unit")
    if query:
        products = products.filter(
            Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode__icontains=query)
        )
    products = list(products.order_by("-created_at"))

    stock_by_product = {
        level.product_id: level.quantity
        for level in StockLevel.objects.filter(boutique=request.boutique, product__in=products)
    }
    for product in products:
        stock_qty = stock_by_product.get(product.id, 0)
        product.is_low_stock = stock_qty <= get_effective_low_stock_threshold(product, request.boutique)
    return render(request, "catalog/product_list.html", {"products": products, "query": query})


@login_required
def product_detail(request, product_id):
    """Fiche produit en lecture seule — distincte de la page de facture :
    un produit est un enregistrement du catalogue, pas une ligne de
    document, et se consulte/gère depuis son propre écran."""
    from apps.stock.models import StockLevel

    product = get_object_or_404(
        Product.objects.select_related("category", "unit").prefetch_related("extra_images"),
        id=product_id,
        compte=request.compte,
    )
    stock_level = StockLevel.objects.filter(boutique=request.boutique, product=product).first()
    stock_qty = stock_level.quantity if stock_level else 0
    return render(
        request,
        "catalog/product_detail.html",
        {
            "product": product,
            "stock_qty": stock_qty,
            "is_low_stock": stock_qty <= get_effective_low_stock_threshold(product, request.boutique),
            "effective_price": get_effective_price(product, request.boutique),
        },
    )


def _save_extra_images(product, files):
    # Boucle avec .save() individuel plutôt que bulk_create() : bulk_create
    # n'exécute pas de façon fiable la sauvegarde physique du fichier dans
    # le stockage pour un ImageField (effet de bord normalement déclenché
    # par pre_save() lors d'un save() classique).
    next_position = product.extra_images.count()
    for i, f in enumerate(files):
        ProductImage.objects.create(product=product, image=f, position=next_position + i)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def product_create(request):
    if not Unit.objects.filter(compte=request.compte).exists():
        messages.warning(
            request, "Créez d'abord au moins une unité de mesure avant d'ajouter un produit."
        )
        return redirect("catalog:unit_create")

    if request.method == "POST":
        form = ProductForm(
            request.POST, request.FILES, compte=request.compte,
            include_quantity=True, require_image=True,
        )
        if form.is_valid():
            product = form.save()
            _save_extra_images(product, form.cleaned_data["extra_images"])

            initial_quantity = form.cleaned_data.get("initial_quantity") or Decimal("0")
            if initial_quantity > 0:
                movement = stock_services.apply_movement(
                    boutique=request.boutique,
                    product=product,
                    type=StockMovement.ENTREE,
                    quantity=initial_quantity,
                    reason="Stock initial",
                    created_by=request.user,
                )
                if settings.IS_OFFLINE:
                    sync_outbox.enqueue(OutboxEntry.STOCK_MOVEMENT, movement.id)

            if settings.IS_OFFLINE:
                sync_outbox.enqueue(OutboxEntry.PRODUCT, product.id)
            messages.success(request, "Produit créé.")
            return redirect("catalog:product_list")
    else:
        form = ProductForm(compte=request.compte, include_quantity=True, require_image=True)
    return render(request, "catalog/product_form.html", {"form": form, "title": "Nouveau produit"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def product_update(request, product_id):
    product = get_object_or_404(Product.objects.prefetch_related("extra_images"), id=product_id, compte=request.compte)
    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=product, compte=request.compte)
        if form.is_valid():
            product = form.save()
            _save_extra_images(product, form.cleaned_data["extra_images"])
            if settings.IS_OFFLINE:
                sync_outbox.enqueue(OutboxEntry.PRODUCT, product.id)
            messages.success(request, "Produit modifié.")
            return redirect("catalog:product_list")
    else:
        form = ProductForm(instance=product, compte=request.compte)
    return render(request, "catalog/product_form.html", {"form": form, "title": "Modifier le produit"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
@block_when_offline("catalog:product_list")
def product_image_delete(request, image_id):
    image = get_object_or_404(ProductImage, id=image_id, product__compte=request.compte)
    product_id = image.product_id
    if request.method == "POST":
        image.image.delete(save=False)
        image.delete()
        messages.success(request, "Photo supprimée.")
    return redirect("catalog:product_update", product_id=product_id)


@login_required
@boutique_role_required(*MANAGE_ROLES)
@block_when_offline("catalog:product_list")
def product_delete(request, product_id):
    product = get_object_or_404(Product, id=product_id, compte=request.compte)
    if request.method == "POST":
        try:
            product.delete()
            messages.success(request, "Produit supprimé.")
        except ProtectedError:
            messages.error(
                request,
                "Impossible de supprimer : ce produit a des mouvements de stock "
                "(ventes, entrées...) liés — désactivez-le plutôt.",
            )
    return redirect("catalog:product_list")


@login_required
def category_list(request):
    categories = Category.objects.filter(compte=request.compte).select_related("parent").order_by("-created_at")
    return render(request, "catalog/category_list.html", {"categories": categories})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def category_create(request):
    if request.method == "POST":
        form = CategoryForm(request.POST, compte=request.compte)
        if form.is_valid():
            category = form.save()
            if settings.IS_OFFLINE:
                sync_outbox.enqueue(OutboxEntry.CATEGORY, category.id)
            messages.success(request, "Catégorie créée.")
            return redirect("catalog:category_list")
    else:
        form = CategoryForm(compte=request.compte)
    return render(request, "catalog/category_form.html", {"form": form, "title": "Nouvelle catégorie"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def category_quick_create(request):
    """Création rapide en JSON, utilisée par la pop-up du formulaire
    produit (voir product_form.html) — évite de quitter la page pour
    ajouter une catégorie manquante. Même logique de sauvegarde que
    category_create, juste une réponse JSON au lieu d'une redirection."""
    if request.method != "POST":
        return JsonResponse({"detail": "Méthode non autorisée."}, status=405)
    form = CategoryForm(request.POST, compte=request.compte)
    if form.is_valid():
        category = form.save()
        if settings.IS_OFFLINE:
            sync_outbox.enqueue(OutboxEntry.CATEGORY, category.id)
        return JsonResponse({"id": str(category.id), "name": category.name})
    return JsonResponse({"errors": form.errors}, status=400)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def unit_quick_create(request):
    """Création rapide en JSON, utilisée par la pop-up du formulaire
    produit (voir product_form.html) — même patron que
    category_quick_create pour l'unité."""
    if request.method != "POST":
        return JsonResponse({"detail": "Méthode non autorisée."}, status=405)
    form = UnitForm(request.POST, compte=request.compte)
    if form.is_valid():
        unit = form.save()
        if settings.IS_OFFLINE:
            sync_outbox.enqueue(OutboxEntry.UNIT, unit.id)
        return JsonResponse({"id": str(unit.id), "name": str(unit)})
    return JsonResponse({"errors": form.errors}, status=400)


@login_required
@boutique_role_required(*MANAGE_ROLES)
def category_update(request, category_id):
    category = get_object_or_404(Category, id=category_id, compte=request.compte)
    if request.method == "POST":
        form = CategoryForm(request.POST, instance=category, compte=request.compte)
        if form.is_valid():
            category = form.save()
            if settings.IS_OFFLINE:
                sync_outbox.enqueue(OutboxEntry.CATEGORY, category.id)
            messages.success(request, "Catégorie modifiée.")
            return redirect("catalog:category_list")
    else:
        form = CategoryForm(instance=category, compte=request.compte)
    return render(request, "catalog/category_form.html", {"form": form, "title": "Modifier la catégorie"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
@block_when_offline("catalog:category_list")
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
    units = Unit.objects.filter(compte=request.compte).order_by("-created_at")
    return render(request, "catalog/unit_list.html", {"units": units})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def unit_create(request):
    if request.method == "POST":
        form = UnitForm(request.POST, compte=request.compte)
        if form.is_valid():
            unit = form.save()
            if settings.IS_OFFLINE:
                sync_outbox.enqueue(OutboxEntry.UNIT, unit.id)
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
            unit = form.save()
            if settings.IS_OFFLINE:
                sync_outbox.enqueue(OutboxEntry.UNIT, unit.id)
            messages.success(request, "Unité modifiée.")
            return redirect("catalog:unit_list")
    else:
        form = UnitForm(instance=unit, compte=request.compte)
    return render(request, "catalog/unit_form.html", {"form": form, "title": "Modifier l'unité"})


@login_required
@boutique_role_required(*MANAGE_ROLES)
@block_when_offline("catalog:unit_list")
def unit_delete(request, unit_id):
    unit = get_object_or_404(Unit, id=unit_id, compte=request.compte)
    if request.method == "POST":
        try:
            unit.delete()
            messages.success(request, "Unité supprimée.")
        except ProtectedError:
            messages.error(request, "Impossible de supprimer : des produits utilisent encore cette unité.")
    return redirect("catalog:unit_list")
