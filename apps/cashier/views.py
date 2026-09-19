from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from apps.core.permissions import block_when_offline, boutique_role_required, compte_admin_required
from apps.sync import outbox
from apps.sync.models import OutboxEntry
from apps.tenants.models import Membership

from . import services
from .forms import CashMovementForm, CashSessionCloseForm, CashSessionOpenForm, CashTransferForm
from .models import CashSession, CashTransfer, PersonalCashMovement

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


# --- Caisse individuelle -------------------------------------------------

@login_required
@boutique_role_required(*CASH_ROLES)
def my_cash(request):
    movements = PersonalCashMovement.objects.filter(
        boutique=request.boutique, user=request.user
    ).select_related("counterparty", "commande")
    pending_received = CashTransfer.objects.filter(
        boutique=request.boutique, to_user=request.user, status=CashTransfer.EN_ATTENTE
    ).select_related("from_user")
    pending_sent = CashTransfer.objects.filter(
        boutique=request.boutique, from_user=request.user, status=CashTransfer.EN_ATTENTE
    ).select_related("to_user")
    return render(
        request, "cashier/my_cash.html",
        {
            "balance": services.personal_cash_balance(request.user, request.boutique),
            "movements": movements[:50],
            "pending_received": pending_received,
            "pending_sent": pending_sent,
            "transfer_form": CashTransferForm(boutique=request.boutique, exclude_user=request.user),
        },
    )


@login_required
@boutique_role_required(*CASH_ROLES)
@block_when_offline("cashier:my_cash")
@require_POST
def cash_transfer_create(request):
    """Crée une demande de transfert — l'argent ne rejoint la caisse du
    destinataire que lorsqu'il l'accepte explicitement (voir
    cash_transfer_accept), pas à la création."""
    form = CashTransferForm(request.POST, boutique=request.boutique, exclude_user=request.user)
    if form.is_valid():
        try:
            services.request_transfer(
                request.boutique,
                from_user=request.user,
                to_user=form.cleaned_data["to_user"],
                amount=form.cleaned_data["amount"],
                reason=form.cleaned_data["reason"],
                created_by=request.user,
            )
            messages.success(
                request,
                _("Demande de transfert de %(amount)s %(currency)s envoyée à %(to)s — en attente de son acceptation.") % {
                    "amount": form.cleaned_data["amount"],
                    "currency": request.boutique.devise,
                    "to": form.cleaned_data["to_user"].get_full_name() or form.cleaned_data["to_user"].email,
                },
            )
        except ValueError as exc:
            messages.error(request, str(exc))
    else:
        messages.error(request, _("Transfert invalide — vérifiez le destinataire et le montant."))
    return redirect("cashier:my_cash")


@login_required
@boutique_role_required(*CASH_ROLES)
@block_when_offline("cashier:my_cash")
@require_POST
def cash_transfer_accept(request, transfer_id):
    cash_transfer = get_object_or_404(
        CashTransfer, id=transfer_id, boutique=request.boutique, to_user=request.user,
    )
    try:
        services.accept_transfer(cash_transfer, decided_by=request.user)
        messages.success(
            request,
            _("%(amount)s %(currency)s ajoutés à votre caisse.") % {
                "amount": cash_transfer.amount, "currency": request.boutique.devise,
            },
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("cashier:my_cash")


@login_required
@boutique_role_required(*CASH_ROLES)
@block_when_offline("cashier:my_cash")
@require_POST
def cash_transfer_reject(request, transfer_id):
    """Refus par le destinataire, ou annulation par l'expéditeur — les
    deux sont autorisés à renoncer à une demande encore en attente."""
    cash_transfer = get_object_or_404(CashTransfer, id=transfer_id, boutique=request.boutique)
    if request.user.pk not in (cash_transfer.to_user_id, cash_transfer.from_user_id):
        messages.error(request, _("Vous n'êtes pas concerné par ce transfert."))
        return redirect("cashier:my_cash")
    services.reject_transfer(cash_transfer, decided_by=request.user)
    messages.success(request, _("Transfert refusé."))
    return redirect("cashier:my_cash")


@login_required
@compte_admin_required
def entreprise_cash_overview(request):
    """État de la caisse individuelle de chaque employé, toutes boutiques
    de l'entreprise confondues — réservé aux administrateurs (voir aussi
    tenants:staff_list, même périmètre boutique__compte=request.compte).
    Un employé garde un solde distinct par boutique où il a accès (l'argent
    physique de sa caisse ne « suit » pas d'une boutique à l'autre).
    Regroupé par boutique plutôt qu'un total global unique : deux
    boutiques d'une même entreprise peuvent avoir des devises différentes,
    additionner leurs soldes n'aurait aucun sens."""
    memberships = (
        Membership.objects.filter(boutique__compte=request.compte, is_active=True)
        .select_related("user", "boutique")
        .order_by("boutique__name", "user__email")
    )

    groups = {}
    for m in memberships:
        group = groups.setdefault(
            m.boutique_id, {"boutique": m.boutique, "rows": [], "subtotal": Decimal("0")}
        )
        balance = services.personal_cash_balance(m.user, m.boutique)
        group["rows"].append({"membership": m, "balance": balance})
        group["subtotal"] += balance

    return render(
        request, "cashier/entreprise_cash.html",
        {"groups": sorted(groups.values(), key=lambda g: g["boutique"].name)},
    )
