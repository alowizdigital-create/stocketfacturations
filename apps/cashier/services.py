import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.core.notifications import notify

from .models import CashMovement, CashSession, CashTransfer, PersonalCashMovement


def _display_name(user):
    return user.get_full_name() or user.email


@transaction.atomic
def open_session(boutique, *, opening_amount, opened_by=None, id=None, opened_at=None):
    """Idempotent par id (rejeu de synchro offline→online) — voir
    apps.stock.services.apply_movement pour le même patron."""
    if id is not None:
        existing = CashSession.objects.filter(pk=id).first()
        if existing is not None:
            return existing

    if CashSession.objects.filter(boutique=boutique, status=CashSession.OUVERTE).exists():
        raise ValueError("Une session de caisse est déjà ouverte pour cette boutique.")

    kwargs = dict(boutique=boutique, opening_amount=opening_amount, opened_by=opened_by, status=CashSession.OUVERTE)
    if id is not None:
        kwargs["id"] = id
    if opened_at is not None:
        kwargs["opened_at"] = opened_at
    session = CashSession(**kwargs)
    session.number = CashSession.generate_number(boutique, session.opened_at)
    session.save()
    return session


@transaction.atomic
def add_movement(session, *, type, amount, reason, created_by=None, id=None, created_at=None,
                  source=CashMovement.SOURCE_ONLINE):
    if id is not None:
        existing = CashMovement.objects.filter(pk=id).first()
        if existing is not None:
            return existing

    kwargs = dict(
        session=session, boutique=session.boutique, type=type, amount=amount,
        reason=reason, created_by=created_by, source=source,
    )
    if id is not None:
        kwargs["id"] = id
    if created_at is not None:
        kwargs["created_at"] = created_at
    return CashMovement.objects.create(**kwargs)


@transaction.atomic
def close_session(session, *, counted_amount, closing_note="", closed_by=None, closed_at=None):
    session.status = CashSession.FERMEE
    session.counted_amount = counted_amount
    session.closing_note = closing_note
    session.closed_by = closed_by
    session.closed_at = closed_at or timezone.now()
    session.save()
    return session


# --- Caisse individuelle -------------------------------------------------
# Un solde par employé, distinct de la session de caisse partagée
# ci-dessus. Jamais stocké : toujours recalculé à partir des paiements en
# espèces qu'il a personnellement encaissés (Payment.created_by) et de ses
# PersonalCashMovement — même philosophie que CashSession.expected_amount().

def personal_cash_balance(user, boutique):
    from apps.sales.models import Payment

    cash_collected = Payment.objects.filter(
        boutique=boutique,
        method=Payment.ESPECES,
        created_by=user,
        invoice__currency=boutique.devise,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    movements = PersonalCashMovement.objects.filter(boutique=boutique, user=user).aggregate(
        credits=Sum("amount", filter=Q(type=PersonalCashMovement.CREDIT)),
        debits=Sum("amount", filter=Q(type=PersonalCashMovement.DEBIT)),
    )
    credits = movements["credits"] or Decimal("0")
    debits = movements["debits"] or Decimal("0")
    return cash_collected + credits - debits


@transaction.atomic
def request_transfer(boutique, *, from_user, to_user, amount, reason="", created_by=None):
    """Crée une demande de transfert en attente — tant que le destinataire
    ne l'a pas acceptée (voir accept_transfer), aucun PersonalCashMovement
    n'est créé : l'argent reste dans la caisse de l'expéditeur. Refuse un
    solde négatif dès la demande — un transfert représente de l'argent
    physique, on ne peut pas en promettre plus qu'on n'en a en caisse (le
    solde est revérifié à l'acceptation, au cas où il aurait changé
    entre-temps)."""

    if from_user.pk == to_user.pk:
        raise ValueError(_("Impossible de transférer vers soi-même."))
    if amount <= 0:
        raise ValueError(_("Le montant doit être positif."))

    balance = personal_cash_balance(from_user, boutique)
    if amount > balance:
        raise ValueError(_("Solde insuffisant dans votre caisse."))

    cash_transfer = CashTransfer.objects.create(
        boutique=boutique, from_user=from_user, to_user=to_user,
        amount=amount, reason=reason, created_by=created_by,
    )
    notify(
        to_user,
        _("%(name)s vous propose un transfert de %(amount)s %(currency)s — cliquez pour accepter ou refuser.") % {
            "name": _display_name(from_user), "amount": amount, "currency": boutique.devise,
        },
        url=reverse("cashier:my_cash"),
    )
    return cash_transfer


@transaction.atomic
def accept_transfer(cash_transfer, *, decided_by):
    """Le destinataire accepte : crée les deux PersonalCashMovement (DEBIT
    chez l'expéditeur, CREDIT chez lui) — c'est seulement à ce moment que
    l'argent s'ajoute réellement à sa caisse. Revérifie le solde de
    l'expéditeur (il a pu baisser depuis la demande, ex: un autre transfert
    accepté entre-temps). Idempotent : ne fait rien si déjà tranché."""

    if cash_transfer.status != CashTransfer.EN_ATTENTE:
        return cash_transfer

    balance = personal_cash_balance(cash_transfer.from_user, cash_transfer.boutique)
    if cash_transfer.amount > balance:
        raise ValueError(_("L'expéditeur n'a plus un solde suffisant pour ce transfert."))

    transfer_marker = uuid.uuid4()
    PersonalCashMovement.objects.create(
        boutique=cash_transfer.boutique, user=cash_transfer.from_user, type=PersonalCashMovement.DEBIT,
        kind=PersonalCashMovement.TRANSFERT, amount=cash_transfer.amount, reason=cash_transfer.reason,
        counterparty=cash_transfer.to_user, transfer_id=transfer_marker, created_by=decided_by,
    )
    PersonalCashMovement.objects.create(
        boutique=cash_transfer.boutique, user=cash_transfer.to_user, type=PersonalCashMovement.CREDIT,
        kind=PersonalCashMovement.TRANSFERT, amount=cash_transfer.amount, reason=cash_transfer.reason,
        counterparty=cash_transfer.from_user, transfer_id=transfer_marker, created_by=decided_by,
    )

    cash_transfer.status = CashTransfer.ACCEPTE
    cash_transfer.decided_at = timezone.now()
    cash_transfer.decided_by = decided_by
    cash_transfer.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])
    notify(
        cash_transfer.from_user,
        _("%(name)s a accepté votre transfert de %(amount)s %(currency)s.") % {
            "name": _display_name(cash_transfer.to_user),
            "amount": cash_transfer.amount, "currency": cash_transfer.boutique.devise,
        },
        url=reverse("cashier:my_cash"),
    )
    return cash_transfer


@transaction.atomic
def reject_transfer(cash_transfer, *, decided_by):
    """Le destinataire refuse (ou l'expéditeur annule) : rien à défaire
    puisqu'aucun PersonalCashMovement n'a jamais été créé pour une demande
    encore en attente. Idempotent — notifie l'autre partie dans les deux
    cas (refus par le destinataire, ou annulation par l'expéditeur)."""

    if cash_transfer.status != CashTransfer.EN_ATTENTE:
        return cash_transfer

    cash_transfer.status = CashTransfer.REFUSE
    cash_transfer.decided_at = timezone.now()
    cash_transfer.decided_by = decided_by
    cash_transfer.save(update_fields=["status", "decided_at", "decided_by", "updated_at"])

    amount_ctx = {"amount": cash_transfer.amount, "currency": cash_transfer.boutique.devise}
    if decided_by is not None and decided_by.pk == cash_transfer.to_user_id:
        notify(
            cash_transfer.from_user,
            _("%(name)s a refusé votre transfert de %(amount)s %(currency)s.") % {
                **amount_ctx, "name": _display_name(cash_transfer.to_user),
            },
            url=reverse("cashier:my_cash"),
        )
    else:
        notify(
            cash_transfer.to_user,
            _("%(name)s a annulé sa proposition de transfert de %(amount)s %(currency)s.") % {
                **amount_ctx, "name": _display_name(cash_transfer.from_user),
            },
            url=reverse("cashier:my_cash"),
        )
    return cash_transfer


def _outstanding_delivery_fees(commande):
    """Frais de livraison encore à la charge de chacun pour cette commande,
    sous forme {user: montant net} (débits - remboursements), limité aux
    montants strictement positifs. Le net, et non le dernier débit : le
    prix de livraison de la boutique a pu changer entre la livraison et son
    annulation, on rembourse ce qui a réellement été prélevé."""
    net = {}
    for movement in PersonalCashMovement.objects.filter(
        commande=commande, kind=PersonalCashMovement.LIVRAISON
    ).select_related("user"):
        sign = 1 if movement.type == PersonalCashMovement.DEBIT else -1
        net[movement.user] = net.get(movement.user, Decimal("0")) + sign * movement.amount
    return {user: amount for user, amount in net.items() if amount > 0}


def charge_delivery_fee(commande, *, delivered_by):
    """Déduit le prix de livraison de la boutique (Boutique.delivery_price)
    de la caisse individuelle de la personne qui livre — appelé depuis
    apps.sales.services.mark_commande_delivered. Ne fait rien si aucun
    prix de livraison n'est configuré, ou si delivered_by est inconnu
    (jamais le cas en pratique, mais mark_commande_delivered n'impose pas
    ce paramètre pour d'autres usages).

    Ne débite jamais deux fois la même livraison : tant que des frais de
    cette commande n'ont pas été remboursés (voir refund_delivery_fee), un
    nouvel appel est sans effet."""

    boutique = commande.boutique
    if not boutique.delivery_price or delivered_by is None:
        return None
    if _outstanding_delivery_fees(commande):
        return None

    return PersonalCashMovement.objects.create(
        boutique=boutique, user=delivered_by, type=PersonalCashMovement.DEBIT,
        kind=PersonalCashMovement.LIVRAISON, amount=boutique.delivery_price,
        reason=_("Livraison %(number)s") % {"number": commande.number},
        commande=commande, created_by=delivered_by,
    )


@transaction.atomic
def refund_delivery_fee(commande, *, refunded_by=None):
    """Rembourse les frais de livraison prélevés pour cette commande —
    appelé quand sa livraison est annulée (voir
    apps.sales.services.mark_commande_en_cours). Crée un CREDIT dans la
    caisse de la personne qui avait été débitée (pas de celle qui annule :
    c'est souvent un administrateur), sans effacer le débit d'origine, pour
    que l'historique montre les deux mouvements. Idempotent : sans frais
    restants à rembourser, ne fait rien. Renvoie les mouvements créés."""

    refunds = []
    for user, amount in _outstanding_delivery_fees(commande).items():
        refunds.append(PersonalCashMovement.objects.create(
            boutique=commande.boutique, user=user, type=PersonalCashMovement.CREDIT,
            kind=PersonalCashMovement.LIVRAISON, amount=amount,
            reason=_("Livraison %(number)s annulée — frais remboursés") % {"number": commande.number},
            commande=commande, created_by=refunded_by,
        ))
    return refunds


def delivery_fees_between(boutique, date_from, date_to):
    """Frais de livraison réellement supportés par la boutique sur la période
    (bornes incluses), pour les compter comme dépense dans le bénéfice.

    Chaque livraison de commande prélève des frais dans la caisse de la
    personne qui livre (charge_delivery_fee) ; une livraison annulée les
    rembourse (refund_delivery_fee). On retient donc, par commande, le NET
    (débits - remboursements), daté du dernier prélèvement — c'est à la
    dernière livraison que le coût est définitivement engagé. Une livraison
    annulée puis non refaite compte zéro ; une commande relivrée n'est comptée
    qu'une fois. Renvoie (total, nombre de commandes)."""
    debits_in_range = PersonalCashMovement.objects.filter(
        boutique=boutique, kind=PersonalCashMovement.LIVRAISON, type=PersonalCashMovement.DEBIT,
        commande__isnull=False, created_at__date__gte=date_from, created_at__date__lte=date_to,
    ).values_list("commande_id", flat=True)

    per_commande = {}
    movements = PersonalCashMovement.objects.filter(
        boutique=boutique, kind=PersonalCashMovement.LIVRAISON, commande_id__in=list(debits_in_range),
    ).order_by("created_at")
    for movement in movements:
        entry = per_commande.setdefault(movement.commande_id, {"net": Decimal("0"), "last_debit": None})
        if movement.type == PersonalCashMovement.DEBIT:
            entry["net"] += movement.amount
            entry["last_debit"] = timezone.localtime(movement.created_at).date()
        else:
            entry["net"] -= movement.amount

    counted = [
        entry["net"] for entry in per_commande.values()
        if entry["net"] > 0 and entry["last_debit"] is not None and date_from <= entry["last_debit"] <= date_to
    ]
    return sum(counted, Decimal("0")), len(counted)
