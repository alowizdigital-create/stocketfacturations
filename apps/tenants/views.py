from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.catalog.models import Unit
from apps.core.permissions import compte_admin_required

from .forms import CompteSettingsForm, SignupForm, StaffCreateForm, StaffUpdateForm
from .models import Boutique, Compte, Membership

User = get_user_model()


def signup(request):
    if request.user.is_authenticated:
        return redirect("core:home")

    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                compte = Compte.objects.create(name=data["entreprise_name"], email=data["email"])
                # Chaque entreprise démarre avec une boutique par défaut,
                # sélectionnée automatiquement à la connexion (voir
                # CurrentTenantMiddleware) — pas besoin de choisir tant
                # qu'il n'y en a qu'une.
                boutique = Boutique.objects.create(
                    compte=compte,
                    name=data["boutique_name"],
                    code=data["boutique_code"].upper(),
                    is_default=True,
                )
                Unit.objects.create(compte=compte, name="Pièce", symbol="pc")
                user = User.objects.create_user(
                    email=data["email"],
                    password=data["password"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                )
                Membership.objects.create(
                    user=user, boutique=boutique, role=Membership.ADMIN_COMPTE
                )

            login(request, user)
            request.session["boutique_id"] = str(boutique.id)
            return redirect("core:home")
    else:
        form = SignupForm()

    return render(request, "tenants/signup.html", {"form": form})


@login_required
def choose_boutique(request):
    memberships = (
        Membership.objects.filter(user=request.user, is_active=True, boutique__is_active=True)
        .select_related("boutique", "boutique__compte")
    )
    return render(request, "tenants/choose_boutique.html", {"memberships": memberships})


@login_required
@require_POST
def set_boutique(request, boutique_id):
    get_object_or_404(
        Boutique,
        id=boutique_id,
        is_active=True,
        memberships__user=request.user,
        memberships__is_active=True,
    )
    request.session["boutique_id"] = str(boutique_id)
    return redirect("core:home")


# --- Gestion du personnel -----------------------------------------------
# Réservé aux administrateurs de l'entreprise (request.is_compte_admin) :
# ce sont eux qui créent les comptes des employés et leur affectent un
# rôle/une boutique — l'accès aux données découle ensuite de ce rôle via
# apps.core.permissions.boutique_role_required, utilisé partout ailleurs.

@login_required
@compte_admin_required
def staff_list(request):
    memberships = (
        Membership.objects.filter(boutique__compte=request.compte)
        .select_related("user", "boutique")
        .order_by("boutique__name", "user__email")
    )
    return render(request, "tenants/staff_list.html", {"memberships": memberships})


@login_required
@compte_admin_required
def staff_create(request):
    if request.method == "POST":
        form = StaffCreateForm(request.POST, compte=request.compte)
        if form.is_valid():
            data = form.cleaned_data
            with transaction.atomic():
                user = User.objects.create_user(
                    email=data["email"],
                    password=data["password"],
                    first_name=data["first_name"],
                    last_name=data["last_name"],
                )
                Membership.objects.create(
                    user=user, boutique=data["boutique"], role=data["role"]
                )
            messages.success(request, f"Compte créé pour {user.email}.")
            return redirect("tenants:staff_list")
    else:
        form = StaffCreateForm(compte=request.compte)
    return render(request, "tenants/staff_form.html", {"form": form, "title": "Nouvel employé"})


@login_required
@compte_admin_required
def staff_update(request, membership_id):
    membership = get_object_or_404(
        Membership.objects.select_related("user", "boutique"),
        id=membership_id,
        boutique__compte=request.compte,
    )
    if membership.user_id == request.user.id:
        messages.error(request, "Vous ne pouvez pas modifier votre propre accès depuis cet écran.")
        return redirect("tenants:staff_list")

    if request.method == "POST":
        form = StaffUpdateForm(request.POST, instance=membership, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(request, f"Accès de {membership.user.email} mis à jour.")
            return redirect("tenants:staff_list")
    else:
        form = StaffUpdateForm(instance=membership, compte=request.compte)
    return render(
        request,
        "tenants/staff_form.html",
        {"form": form, "title": f"Modifier l'accès de {membership.user.email}"},
    )


@login_required
@compte_admin_required
def company_settings(request):
    compte = request.compte
    if request.method == "POST":
        form = CompteSettingsForm(request.POST, request.FILES, instance=compte)
        if form.is_valid():
            form.save()
            messages.success(request, "Paramètres de l'entreprise mis à jour.")
            return redirect("tenants:company_settings")
    else:
        form = CompteSettingsForm(instance=compte)
    return render(request, "tenants/company_settings.html", {"form": form})
