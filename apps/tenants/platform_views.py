"""Administration de la PLATEFORME : vue d'ensemble de toutes les entreprises
et de tous les utilisateurs, réservée au super-administrateur (is_superuser).
Distinct de l'espace « Administration » d'une entreprise (Équipe, Paramètres),
qui ne voit que sa propre entreprise."""

from collections import Counter

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.core.permissions import platform_admin_required
from apps.sales.models import Invoice, Payment, Sale
from apps.stock.models import StockMovement

from .models import Boutique, Compte, Membership
from .platform_forms import PlatformPasswordForm, PlatformUserForm

User = get_user_model()

PAGE_SIZE = 25

# Ce qui compte comme "utiliser l'application" : les actions qui laissent une
# trace `created_by`, tous comptes/boutiques confondus (vue plateforme, donc
# volontairement pas filtrée par entreprise). Ventes et lignes de caisse sont
# les gestes les plus fréquents d'un utilisateur actif ; mouvements de stock
# et paiements captent aussi l'activité côté gestion. Pas de piste d'audit
# dédiée dans l'app — ce total est un indicateur, pas une mesure exacte du
# temps passé ni des connexions.
ACTIVITY_MODELS = [Sale, Invoice, StockMovement, Payment]


def _render(request, template, context):
    # platform_layout : demande à base.html d'afficher la coquille de
    # l'application même sans boutique sélectionnée (un super-admin n'en a
    # souvent aucune).
    return render(request, template, {**context, "platform_layout": True})


@login_required
@platform_admin_required
def platform_dashboard(request):
    query = request.GET.get("q", "").strip()

    companies = (
        Compte.objects.annotate(
            nb_boutiques=Count("boutiques", distinct=True),
            nb_employes=Count(
                "boutiques__memberships__user",
                filter=Q(boutiques__memberships__is_active=True),
                distinct=True,
            ),
        )
        .select_related("subscription__plan")
        .order_by("name")
    )
    if query:
        companies = companies.filter(Q(name__icontains=query) | Q(email__icontains=query))

    page = Paginator(companies, PAGE_SIZE).get_page(request.GET.get("page"))

    admins_by_compte = {}
    for compte_id, email in Membership.objects.filter(
        role=Membership.ADMIN_COMPTE, is_active=True,
        boutique__compte__in=[c.id for c in page],
    ).values_list("boutique__compte_id", "user__email").distinct():
        admins_by_compte.setdefault(compte_id, []).append(email)
    for company in page:
        company.admin_emails = sorted(admins_by_compte.get(company.id, []))

    stats = {
        "nb_users": User.objects.count(),
        "nb_users_actifs": User.objects.filter(is_active=True).count(),
        "nb_entreprises": Compte.objects.count(),
        "nb_boutiques": Boutique.objects.count(),
    }
    return _render(
        request, "tenants/platform/dashboard.html",
        {"stats": stats, "page": page, "query": query},
    )


@login_required
@platform_admin_required
def platform_user_list(request):
    query = request.GET.get("q", "").strip()

    users = User.objects.prefetch_related("memberships__boutique__compte").order_by("email")
    if query:
        users = users.filter(
            Q(email__icontains=query) | Q(first_name__icontains=query) | Q(last_name__icontains=query)
        )

    page = Paginator(users, PAGE_SIZE).get_page(request.GET.get("page"))
    for user in page:
        user.company_names = sorted({m.boutique.compte.name for m in user.memberships.all()})

    return _render(
        request, "tenants/platform/user_list.html",
        {"page": page, "query": query, "total": User.objects.count()},
    )


def _top_active_users(limit=10):
    """Les utilisateurs les plus actifs, tous comptes confondus : compte le
    nombre d'actions (ventes, devis/commandes/factures, mouvements de
    stock, paiements) créées par chacun, additionné sur ACTIVITY_MODELS,
    puis garde les `limit` plus grands totaux. Une requête d'agrégation
    par modèle (pas une jointure géante) — sommées en mémoire, ce qui reste
    largement suffisant vu le nombre d'utilisateurs attendu."""
    totals = Counter()
    for model in ACTIVITY_MODELS:
        rows = (
            model.objects.exclude(created_by__isnull=True)
            .values("created_by").annotate(n=Count("id"))
        )
        for row in rows:
            totals[row["created_by"]] += row["n"]

    top = totals.most_common(limit)
    users = User.objects.in_bulk([user_id for user_id, _count in top])
    ranking = []
    for user_id, count in top:
        user = users.get(user_id)
        if user is None:
            continue  # utilisateur supprimé depuis (created_by=SET_NULL sinon)
        ranking.append({"user": user, "count": count})

    for row in ranking:
        row["company_names"] = sorted({
            m.boutique.compte.name
            for m in row["user"].memberships.filter(is_active=True).select_related("boutique__compte")
        })
    return ranking


@login_required
@platform_admin_required
def platform_top_users(request):
    """Classement des 10 utilisateurs les plus actifs de la plateforme
    (voir _top_active_users) — réservé au super-administrateur, jamais
    visible depuis l'espace d'une entreprise."""
    return _render(request, "tenants/platform/top_users.html", {"ranking": _top_active_users(10)})


@login_required
@platform_admin_required
def platform_user_edit(request, user_id):
    target = get_object_or_404(User.objects.prefetch_related("memberships__boutique__compte"), pk=user_id)
    info_form = PlatformUserForm(instance=target)
    password_form = PlatformPasswordForm(user=target)

    if request.method == "POST":
        if request.POST.get("form_name") == "password":
            password_form = PlatformPasswordForm(request.POST, user=target)
            if password_form.is_valid():
                target.set_password(password_form.cleaned_data["new_password1"])
                target.save()
                if target.pk == request.user.pk:
                    # Sinon le changement de son propre mot de passe la
                    # déconnecterait immédiatement.
                    update_session_auth_hash(request, target)
                messages.success(
                    request,
                    _("Mot de passe de %(email)s modifié — ses sessions ouvertes ont été déconnectées.")
                    % {"email": target.email},
                )
                return redirect("platform_admin:user_edit", user_id=target.pk)
        else:
            info_form = PlatformUserForm(request.POST, instance=target)
            if info_form.is_valid():
                if target.pk == request.user.pk and not info_form.cleaned_data["is_active"]:
                    info_form.add_error("is_active", _("Vous ne pouvez pas désactiver votre propre compte."))
                else:
                    info_form.save()
                    messages.success(request, _("Informations de %(email)s mises à jour.") % {"email": target.email})
                    return redirect("platform_admin:user_edit", user_id=target.pk)

    memberships = sorted(
        target.memberships.all(), key=lambda m: (m.boutique.compte.name, m.boutique.name)
    )
    return _render(
        request, "tenants/platform/user_edit.html",
        {
            "target": target, "info_form": info_form, "password_form": password_form,
            "memberships": memberships,
        },
    )
