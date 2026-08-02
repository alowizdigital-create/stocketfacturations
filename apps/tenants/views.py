from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.catalog.models import Unit

from .forms import SignupForm
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
