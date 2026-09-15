from django.db import transaction
from django.utils import timezone

from .models import CashMovement, CashSession


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
