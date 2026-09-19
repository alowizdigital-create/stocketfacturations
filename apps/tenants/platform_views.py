"""Administration de la PLATEFORME : vue d'ensemble de toutes les entreprises
et de tous les utilisateurs, réservée au super-administrateur (is_superuser).
Distinct de l'espace « Administration » d'une entreprise (Équipe, Paramètres),
qui ne voit que sa propre entreprise."""

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from apps.core.permissions import platform_admin_required

from .models import Boutique, Compte, Membership
from .platform_forms import PlatformPasswordForm, PlatformUserForm

User = get_user_model()

PAGE_SIZE = 25


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
