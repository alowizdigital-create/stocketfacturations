import json
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST
from django.utils.translation import gettext as _

from apps.catalog.models import Product
from apps.core.permissions import block_when_offline, boutique_role_required
from apps.tenants.models import Membership

from . import services
from .forms import ExpenseForm, PurchaseHeaderForm, SupplierForm
from .models import Expense, Purchase, Supplier

# Les prix d'achat et les fournisseurs n'ont rien à faire sous les yeux d'un
# caissier : réservé aux administrateurs et gérants (comme le catalogue).
MANAGE_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE)


# --- Fournisseurs ---------------------------------------------------------

@login_required
@boutique_role_required(*MANAGE_ROLES)
def supplier_list(request):
    query = request.GET.get("q", "").strip()
    suppliers = Supplier.objects.filter(compte=request.compte).annotate(nb_purchases=Count("purchases"))
    if query:
        suppliers = suppliers.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query))
    return render(request, "purchasing/supplier_list.html", {"suppliers": suppliers, "query": query})


@login_required
@boutique_role_required(*MANAGE_ROLES)
@block_when_offline("purchasing:supplier_list")
def supplier_create(request):
    if request.method == "POST":
        form = SupplierForm(request.POST, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(request, _("Fournisseur créé."))
            return redirect("purchasing:supplier_list")
    else:
        form = SupplierForm(compte=request.compte)
    return render(request, "purchasing/supplier_form.html", {"form": form, "title": _("Nouveau fournisseur")})


@login_required
@boutique_role_required(*MANAGE_ROLES)
@block_when_offline("purchasing:supplier_list")
def supplier_update(request, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id, compte=request.compte)
    if request.method == "POST":
        form = SupplierForm(request.POST, instance=supplier, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(request, _("Fournisseur modifié."))
            return redirect("purchasing:supplier_list")
    else:
        form = SupplierForm(instance=supplier, compte=request.compte)
    return render(request, "purchasing/supplier_form.html", {"form": form, "title": _("Modifier le fournisseur")})


@login_required
@boutique_role_required(*MANAGE_ROLES)
@block_when_offline("purchasing:supplier_list")
def supplier_quick_create(request):
    """Création rapide en JSON depuis la pop-up du formulaire de réception —
    même patron que sales:client_quick_create."""
    if request.method != "POST":
        return JsonResponse({"detail": _("Méthode non autorisée.")}, status=405)
    # La pop-up n'envoie que nom et téléphone : sans ceci, la case « actif »
    # absente du POST créerait un fournisseur inactif, aussitôt refusé par la
    # réception qui l'a créé.
    data = request.POST.copy()
    data["is_active"] = "on"
    form = SupplierForm(data, compte=request.compte)
    if form.is_valid():
        supplier = form.save()
        return JsonResponse({"id": str(supplier.id), "name": supplier.name})
    return JsonResponse({"errors": form.errors}, status=400)


# --- Réceptions de marchandises ------------------------------------------

def _parse_lines(request):
    """Décode les lignes envoyées par l'écran de réception (JSON construit
    côté JS). Le produit est toujours relu en base et restreint à
    l'entreprise ; seuls la quantité et le coût viennent du client."""
    try:
        items = json.loads(request.POST.get("lines_json", "") or "[]")
    except json.JSONDecodeError:
        return None, _("Lignes invalides.")
    if not isinstance(items, list) or not items:
        return None, _("Ajoutez au moins un produit à la réception.")

    products = {
        str(p.id): p
        for p in Product.objects.filter(
            compte=request.compte, is_active=True, id__in=[i.get("product_id") for i in items if isinstance(i, dict)]
        )
    }
    lines = []
    for item in items:
        product = products.get(str(item.get("product_id"))) if isinstance(item, dict) else None
        if product is None:
            return None, _("Un produit de la réception n'existe plus.")
        try:
            quantity = Decimal(str(item.get("quantity", "0")))
            unit_cost = Decimal(str(item.get("unit_cost", "")))
        except InvalidOperation:
            return None, _("Quantité ou coût invalide.")
        if quantity <= 0 or quantity != quantity.to_integral_value():
            return None, _("La quantité doit être un nombre entier positif.")
        if unit_cost < 0 or unit_cost != unit_cost.to_integral_value():
            return None, _("Le coût unitaire doit être un nombre entier positif ou nul.")
        lines.append({"product": product, "quantity": quantity, "unit_cost": unit_cost})
    return lines, None


@login_required
@boutique_role_required(*MANAGE_ROLES)
def purchase_list(request):
    query = request.GET.get("q", "").strip()
    purchases = (
        Purchase.objects.filter(boutique=request.boutique)
        .select_related("supplier").annotate(nb_lines=Count("lines"))
    )
    if query:
        purchases = purchases.filter(
            Q(number__icontains=query) | Q(reference__icontains=query) | Q(supplier__name__icontains=query)
        )
    else:
        purchases = purchases[:50]
    return render(request, "purchasing/purchase_list.html", {"purchases": purchases, "query": query})


@login_required
@boutique_role_required(*MANAGE_ROLES)
@block_when_offline("purchasing:purchase_list")
def purchase_create(request):
    """Réception de marchandises : alimente le stock de la boutique courante
    et met à jour les prix d'achat (voir services.receive_goods)."""
    if request.method == "POST":
        form = PurchaseHeaderForm(request.POST, compte=request.compte)
        lines, error = _parse_lines(request)
        if error:
            messages.error(request, error)
        elif form.is_valid():
            try:
                purchase = services.receive_goods(
                    boutique=request.boutique,
                    lines=lines,
                    supplier=form.cleaned_data["supplier"],
                    received_date=form.cleaned_data["received_date"],
                    reference=form.cleaned_data["reference"],
                    note=form.cleaned_data["note"],
                    created_by=request.user,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    _("%(number)s enregistrée : %(count)s produit(s) ajouté(s) au stock.")
                    % {"number": purchase.number, "count": len(lines)},
                )
                return redirect("purchasing:purchase_detail", purchase_id=purchase.id)
    else:
        form = PurchaseHeaderForm(compte=request.compte)
    # Après une erreur, les lignes déjà saisies sont renvoyées à l'écran
    # (nom et unité inclus : le JSON posté ne contient que des identifiants).
    posted_lines = [
        {
            "product_id": str(line["product"].id), "name": line["product"].name,
            "unit": str(line["product"].unit), "quantity": int(line["quantity"]),
            "unit_cost": int(line["unit_cost"]),
        }
        for line in (lines or [])
    ] if request.method == "POST" else []
    return render(request, "purchasing/purchase_form.html", {"form": form, "posted_lines": posted_lines})


@login_required
@boutique_role_required(*MANAGE_ROLES)
def purchase_detail(request, purchase_id):
    purchase = get_object_or_404(
        Purchase.objects.select_related("supplier", "created_by"), id=purchase_id, boutique=request.boutique,
    )
    return render(
        request, "purchasing/purchase_detail.html",
        {"purchase": purchase, "lines": purchase.lines.select_related("product")},
    )


# --- Dépenses de fonctionnement -------------------------------------------

@login_required
@boutique_role_required(*MANAGE_ROLES)
def expense_list(request):
    """Dépenses de la boutique sur une période (par défaut : le mois en
    cours), avec le total par catégorie. Les dépenses annulées restent
    visibles mais hors des totaux."""
    today = timezone.localdate()
    date_from = parse_date(request.GET.get("from", "") or "") or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "") or "") or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    category = request.GET.get("category", "")

    expenses = Expense.objects.filter(
        boutique=request.boutique, expense_date__gte=date_from, expense_date__lte=date_to,
    ).select_related("created_by")
    if category in dict(Expense.CATEGORY_CHOICES):
        expenses = expenses.filter(category=category)
    else:
        category = ""
    expenses = list(expenses)

    active = [e for e in expenses if not e.is_cancelled]
    by_category = {}
    for expense in active:
        by_category[expense.category] = by_category.get(expense.category, 0) + expense.amount
    labels = dict(Expense.CATEGORY_CHOICES)
    breakdown = [(labels[k], v) for k, v in by_category.items()]
    total = sum((e.amount for e in active), 0)
    # Frais de livraison (prélevés à chaque livraison de commande) : comptés
    # comme dépense mais non saisis ici — affichés en lecture seule, hors
    # filtre de catégorie (ce ne sont pas des dépenses saisies).
    delivery_total, delivery_count = 0, 0
    if not category:
        from apps.cashier.services import delivery_fees_between

        delivery_total, delivery_count = delivery_fees_between(request.boutique, date_from, date_to)
        if delivery_total > 0:
            breakdown.append((_("Frais de livraison"), delivery_total))
            total += delivery_total
    breakdown.sort(key=lambda item: -item[1])
    return render(request, "purchasing/expense_list.html", {
        "expenses": expenses, "total": total, "breakdown": breakdown,
        "delivery_total": delivery_total, "delivery_count": delivery_count,
        "date_from": date_from, "date_to": date_to, "category": category,
        "categories": Expense.CATEGORY_CHOICES,
    })


@login_required
@boutique_role_required(*MANAGE_ROLES)
@block_when_offline("purchasing:expense_list")
def expense_create(request):
    if request.method == "POST":
        form = ExpenseForm(request.POST)
        if form.is_valid():
            try:
                services.record_expense(
                    boutique=request.boutique, created_by=request.user, **form.cleaned_data,
                )
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, _("Dépense enregistrée."))
                return redirect("purchasing:expense_list")
    else:
        form = ExpenseForm()
    from apps.cashier.services import personal_cash_balance

    return render(request, "purchasing/expense_form.html", {
        "form": form, "balance": personal_cash_balance(request.user, request.boutique),
    })


@login_required
@boutique_role_required(Membership.ADMIN_COMPTE)
@block_when_offline("purchasing:expense_list")
@require_POST
def expense_cancel(request, expense_id):
    """Seul l'administrateur annule une dépense (une erreur de saisie se
    corrige par annulation puis nouvelle saisie)."""
    expense = get_object_or_404(Expense, id=expense_id, boutique=request.boutique)
    services.cancel_expense(expense, cancelled_by=request.user)
    messages.success(request, _("Dépense annulée."))
    return redirect("purchasing:expense_list")
