from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.permissions import boutique_role_required
from apps.sync import outbox
from apps.sync.models import OutboxEntry
from apps.tenants.models import Membership

from . import services
from .forms import CashMovementForm, CashSessionCloseForm, CashSessionOpenForm
from .models import CashSession

# La caisse est le cœur du métier d'un caissier : les trois rôles y ont
# tous accès (contrairement à MANAGE_ROLES ailleurs, qui exclut CAISSIER).
CASH_ROLES = (Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE, Membership.CAISSIER)


@login_required
@boutique_role_required(*CASH_ROLES)
def cash_session_current(request):
    session = CashSession.objects.filter(boutique=request.boutique, status=CashSession.OUVERTE).first()
    if session is not None:
        return redirect("cashier:session_detail", session_id=session.id)
    return redirect("cashier:session_open")


@login_required
@boutique_role_required(*CASH_ROLES)
def cash_session_open(request):
    existing = CashSession.objects.filter(boutique=request.boutique, status=CashSession.OUVERTE).first()
    if existing is not None:
        return redirect("cashier:session_detail", session_id=existing.id)

    if request.method == "POST":
        form = CashSessionOpenForm(request.POST)
        if form.is_valid():
            session = services.open_session(
                request.boutique,
                opening_amount=form.cleaned_data["opening_amount"],
                opened_by=request.user,
            )
            if settings.IS_OFFLINE:
                outbox.enqueue(OutboxEntry.CASH_SESSION, session.id)
            messages.success(request, _("Caisse ouverte."))
            return redirect("cashier:session_detail", session_id=session.id)
    else:
        form = CashSessionOpenForm()
    return render(request, "cashier/cash_session_open.html", {"form": form})


@login_required
@boutique_role_required(*CASH_ROLES)
def cash_session_detail(request, session_id):
    session = get_object_or_404(CashSession, id=session_id, boutique=request.boutique)
    movement_form = CashMovementForm()
    close_form = CashSessionCloseForm()
    return render(
        request, "cashier/cash_session_detail.html",
        {
            "session": session,
            "movements": session.movements.select_related("created_by"),
            "expected_amount": session.expected_amount(),
            "movement_form": movement_form,
            "close_form": close_form,
        },
    )


@login_required
@boutique_role_required(*CASH_ROLES)
@require_POST
def cash_movement_create(request, session_id):
    session = get_object_or_404(
        CashSession, id=session_id, boutique=request.boutique, status=CashSession.OUVERTE
    )
    form = CashMovementForm(request.POST)
    if form.is_valid():
        movement = services.add_movement(
            session,
            type=form.cleaned_data["type"],
            amount=form.cleaned_data["amount"],
            reason=form.cleaned_data["reason"],
            created_by=request.user,
        )
        if settings.IS_OFFLINE:
            outbox.enqueue(OutboxEntry.CASH_MOVEMENT, movement.id)
        messages.success(request, _("Mouvement enregistré."))
    else:
        messages.error(request, _("Mouvement invalide — vérifiez le montant et le motif."))
    return redirect("cashier:session_detail", session_id=session.id)


@login_required
@boutique_role_required(*CASH_ROLES)
@require_POST
def cash_session_close(request, session_id):
    session = get_object_or_404(
        CashSession, id=session_id, boutique=request.boutique, status=CashSession.OUVERTE
    )
    form = CashSessionCloseForm(request.POST)
    if form.is_valid():
        services.close_session(
            session,
            counted_amount=form.cleaned_data["counted_amount"],
            closing_note=form.cleaned_data["closing_note"],
            closed_by=request.user,
        )
        if settings.IS_OFFLINE:
            outbox.enqueue(OutboxEntry.CASH_SESSION, session.id)
        messages.success(request, _("Caisse fermée."))
    else:
        messages.error(request, _("Montant compté invalide."))
    return redirect("cashier:session_detail", session_id=session.id)


@login_required
def cash_session_list(request):
    query = request.GET.get("q", "").strip()
    sessions = CashSession.objects.filter(boutique=request.boutique)
    if query:
        sessions = sessions.filter(number__icontains=query)
    if not query:
        sessions = sessions[:10]
    return render(request, "cashier/cash_session_list.html", {"sessions": sessions, "query": query})
