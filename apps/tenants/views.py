from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.catalog.models import Unit
from apps.core.permissions import block_when_offline, compte_admin_required

from .forms import (
    BoutiqueRegionalForm,
    CompteSettingsForm,
    ExchangeRateForm,
    SignupForm,
    StaffCreateForm,
    StaffUpdateForm,
    SubscriptionForm,
)
from .models import BoutiqueAPIToken, Boutique, Compte, ExchangeRate, Membership, Plan, Subscription

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

                entreprise_name = data["entreprise_name"].strip()
                boutique_code = entreprise_name.replace(" ", "")[:3].upper()

                boutique = Boutique.objects.create(
                    compte=compte,
                    name=data["boutique_name"],
                    code=boutique_code,
                    devise=data["devise"],
                    is_default=True,
                )
                Unit.objects.create(compte=compte, name="Pièce", symbol="pc")
                free_plan = Plan.objects.filter(name="Gratuit").first()
                if free_plan:
                    Subscription.objects.create(compte=compte, plan=free_plan)
                user = User.objects.create_user(
                    email=data["email"],
                    password=data["password"],
                    # first_name=data["first_name"],
                    # last_name=data["last_name"],
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
    query = request.GET.get("q", "").strip()
    memberships = Membership.objects.filter(boutique__compte=request.compte).select_related("user", "boutique")
    if query:
        memberships = memberships.filter(
            Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
            | Q(user__email__icontains=query)
        )
    memberships = memberships.order_by("-created_at")
    if not query:
        memberships = memberships[:10]
    return render(request, "tenants/staff_list.html", {"memberships": memberships, "query": query})


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
            messages.success(request, _("Compte créé pour %(email)s.") % {"email": user.email})
            return redirect("tenants:staff_list")
    else:
        form = StaffCreateForm(compte=request.compte)
    return render(request, "tenants/staff_form.html", {"form": form, "title": _("Nouvel employé")})


@login_required
@compte_admin_required
def staff_update(request, membership_id):
    membership = get_object_or_404(
        Membership.objects.select_related("user", "boutique"),
        id=membership_id,
        boutique__compte=request.compte,
    )
    if membership.user_id == request.user.id:
        messages.error(request, _("Vous ne pouvez pas modifier votre propre accès depuis cet écran."))
        return redirect("tenants:staff_list")

    if request.method == "POST":
        form = StaffUpdateForm(request.POST, instance=membership, compte=request.compte)
        if form.is_valid():
            form.save()
            messages.success(
                request, _("Accès de %(email)s mis à jour.") % {"email": membership.user.email}
            )
            return redirect("tenants:staff_list")
    else:
        form = StaffUpdateForm(instance=membership, compte=request.compte)
    return render(
        request,
        "tenants/staff_form.html",
        {"form": form, "title": _("Modifier l'accès de %(email)s") % {"email": membership.user.email}},
    )


@login_required
@compte_admin_required
def company_settings(request):
    compte = request.compte
    boutique = request.boutique

    # L'entreprise/la boutique sont des ressources "pull-only" (aucun
    # endpoint de push, voir apps.sync.outbox.PUSH_ENDPOINTS) : un
    # changement fait ici hors-ligne serait accepté en apparence puis
    # silencieusement écrasé par le prochain pull (qui republie toujours
    # la version du serveur) — jamais synchronisé, jamais perdu proprement.
    # Consultation en lecture seule laissée possible, seule la sauvegarde
    # est bloquée.
    if request.method == "POST" and settings.IS_OFFLINE:
        messages.error(
            request,
            _(
                "Les paramètres de l'entreprise/boutique se gèrent uniquement depuis le poste "
                "en ligne — un changement fait ici ne serait jamais synchronisé."
            ),
        )
        return redirect("tenants:company_settings")

    if request.method == "POST" and request.POST.get("form_name") == "boutique" and boutique is not None:
        form = CompteSettingsForm(instance=compte)
        boutique_form = BoutiqueRegionalForm(request.POST, instance=boutique)
        if boutique_form.is_valid():
            boutique_form.save()
            messages.success(request, _("Paramètres régionaux de la boutique mis à jour."))
            return redirect("tenants:company_settings")
    elif request.method == "POST":
        form = CompteSettingsForm(request.POST, request.FILES, instance=compte)
        boutique_form = BoutiqueRegionalForm(instance=boutique) if boutique is not None else None
        if form.is_valid():
            form.save()
            messages.success(request, _("Paramètres de l'entreprise mis à jour."))
            return redirect("tenants:company_settings")
    else:
        form = CompteSettingsForm(instance=compte)
        boutique_form = BoutiqueRegionalForm(instance=boutique) if boutique is not None else None

    return render(
        request,
        "tenants/company_settings.html",
        {"form": form, "boutique_form": boutique_form},
    )


@login_required
@compte_admin_required
def exchange_rate_list(request):
    boutique = request.boutique
    if boutique is None:
        messages.error(request, _("Aucune boutique sélectionnée."))
        return redirect("tenants:company_settings")

    # Les taux de change sont "pull-only" (aucun endpoint de push) — même
    # raison que company_settings : un ajout fait ici hors-ligne serait
    # écrasé sans avertissement au prochain pull.
    if request.method == "POST" and settings.IS_OFFLINE:
        messages.error(
            request,
            _(
                "Les taux de change se gèrent uniquement depuis le poste en ligne — "
                "un ajout fait ici ne serait jamais synchronisé."
            ),
        )
        return redirect("tenants:exchange_rate_list")

    if request.method == "POST":
        form = ExchangeRateForm(request.POST, boutique=boutique)
        if form.is_valid():
            form.save()
            messages.success(request, _("Taux de change ajouté."))
            return redirect("tenants:exchange_rate_list")
    else:
        form = ExchangeRateForm(boutique=boutique)

    rates = boutique.exchange_rates.all()
    return render(
        request,
        "tenants/exchange_rate_list.html",
        {"form": form, "rates": rates, "boutique": boutique},
    )


@login_required
@compte_admin_required
@block_when_offline("tenants:exchange_rate_list")
def exchange_rate_delete(request, rate_id):
    rate = get_object_or_404(ExchangeRate, id=rate_id, boutique__compte=request.compte)
    if request.method == "POST":
        rate.delete()
        messages.success(request, _("Taux de change supprimé."))
    return redirect("tenants:exchange_rate_list")


@login_required
@compte_admin_required
def boutique_token(request):
    """Écran de synchronisation hors-ligne : montre le statut du jeton
    actuel de la boutique (BoutiqueAPIToken) — le poste offline s'active en
    collant ce jeton une fois au premier lancement, voir apps.sync."""

    boutique = request.boutique
    if boutique is None:
        messages.error(request, _("Aucune boutique sélectionnée."))
        return redirect("tenants:company_settings")

    token = BoutiqueAPIToken.objects.filter(boutique=boutique).first()
    return render(
        request, "tenants/boutique_token.html",
        {"boutique": boutique, "token": token, "raw_token": None},
    )


@login_required
@compte_admin_required
@require_POST
def boutique_token_generate(request):
    boutique = request.boutique
    if boutique is None:
        messages.error(request, _("Aucune boutique sélectionnée."))
        return redirect("tenants:company_settings")

    token, raw_token = BoutiqueAPIToken.issue(boutique)
    messages.warning(
        request, _("Notez ce jeton maintenant : il ne sera plus jamais affiché en clair.")
    )
    return render(
        request, "tenants/boutique_token.html",
        {"boutique": boutique, "token": token, "raw_token": raw_token},
    )


@login_required
@compte_admin_required
def subscription_view(request):
    subscription, _created = Subscription.objects.get_or_create(
        compte=request.compte,
        defaults={"plan": Plan.objects.filter(is_active=True).order_by("price_monthly").first()},
    )
    if request.method == "POST":
        form = SubscriptionForm(request.POST, instance=subscription)
        if form.is_valid():
            form.save()
            messages.success(request, _("Abonnement mis à jour."))
            return redirect("tenants:subscription")
    else:
        form = SubscriptionForm(instance=subscription)

    plans = Plan.objects.filter(is_active=True)
    return render(
        request,
        "tenants/subscription.html",
        {"form": form, "subscription": subscription, "plans": plans},
    )
