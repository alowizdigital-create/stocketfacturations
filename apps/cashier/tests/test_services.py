from decimal import Decimal

import pytest
from django.utils import timezone

from apps.sales.models import Invoice, Payment
from apps.sync.tests.factories import BoutiqueFactory

from .. import services
from ..models import CashMovement, CashSession

pytestmark = pytest.mark.django_db


def _invoice(boutique, currency=None):
    return Invoice.objects.create(
        boutique=boutique,
        number=f"TEST-{Invoice.objects.count() + 1}",
        type=Invoice.FACTURE,
        status=Invoice.PAYEE,
        currency=currency or boutique.devise,
        subtotal_ht=Decimal("1000"),
        total_tva=Decimal("0"),
        total_ttc=Decimal("1000"),
    )


def _cash_payment(boutique, amount, invoice=None):
    return Payment.objects.create(
        invoice=invoice or _invoice(boutique),
        boutique=boutique,
        amount=amount,
        method=Payment.ESPECES,
    )


def test_open_session_creates_with_generated_number():
    boutique = BoutiqueFactory()
    session = services.open_session(boutique, opening_amount=Decimal("5000"))
    assert session.status == CashSession.OUVERTE
    assert session.number.startswith(f"{boutique.code}-CAISSE-")


def test_open_session_rejects_second_open_session():
    boutique = BoutiqueFactory()
    services.open_session(boutique, opening_amount=Decimal("5000"))
    with pytest.raises(ValueError):
        services.open_session(boutique, opening_amount=Decimal("1000"))


def test_open_session_idempotent_replay_by_id():
    boutique = BoutiqueFactory()
    session = services.open_session(boutique, opening_amount=Decimal("5000"))
    replayed = services.open_session(boutique, opening_amount=Decimal("9999"), id=session.id)
    assert replayed.pk == session.pk
    assert replayed.opening_amount == Decimal("5000")  # pas écrasé par le rejeu
    assert CashSession.objects.filter(boutique=boutique).count() == 1


def test_add_movement_idempotent_replay_by_id():
    boutique = BoutiqueFactory()
    session = services.open_session(boutique, opening_amount=Decimal("5000"))
    movement = services.add_movement(session, type=CashMovement.SORTIE, amount=Decimal("500"), reason="Course")
    replayed = services.add_movement(
        session, type=CashMovement.ENTREE, amount=Decimal("1"), reason="autre", id=movement.id
    )
    assert replayed.pk == movement.pk
    assert replayed.type == CashMovement.SORTIE  # pas écrasé
    assert CashMovement.objects.filter(session=session).count() == 1


def test_expected_amount_combines_cash_sales_and_manual_movements():
    boutique = BoutiqueFactory()
    session = services.open_session(boutique, opening_amount=Decimal("10000"))

    _cash_payment(boutique, Decimal("2000"))
    _cash_payment(boutique, Decimal("1000"))
    # Paiement non-espèces : ne doit pas compter.
    other_invoice = _invoice(boutique)
    Payment.objects.create(invoice=other_invoice, boutique=boutique, amount=Decimal("5000"), method=Payment.MOBILE_MONEY)

    services.add_movement(session, type=CashMovement.ENTREE, amount=Decimal("500"), reason="Appoint")
    services.add_movement(session, type=CashMovement.SORTIE, amount=Decimal("300"), reason="Course")

    # 10000 (ouverture) + 3000 (ventes espèces) + 500 (entrée) - 300 (sortie)
    assert session.expected_amount() == Decimal("13200")


def test_expected_amount_ignores_cash_sales_outside_session_window():
    boutique = BoutiqueFactory()
    session = services.open_session(boutique, opening_amount=Decimal("0"))
    payment = _cash_payment(boutique, Decimal("5000"))
    payment.paid_at = timezone.now() - timezone.timedelta(days=1)
    payment.save(update_fields=["paid_at"])

    assert session.expected_amount() == Decimal("0")


def test_expected_amount_ignores_cash_sales_in_foreign_currency():
    boutique = BoutiqueFactory()
    session = services.open_session(boutique, opening_amount=Decimal("0"))
    _cash_payment(boutique, Decimal("5000"), invoice=_invoice(boutique, currency="EUR"))

    assert session.expected_amount() == Decimal("0")


def test_close_session_records_counted_amount_and_discrepancy():
    boutique = BoutiqueFactory()
    session = services.open_session(boutique, opening_amount=Decimal("10000"))
    services.close_session(session, counted_amount=Decimal("9500"), closing_note="Manque constaté")

    session.refresh_from_db()
    assert session.status == CashSession.FERMEE
    assert session.discrepancy == Decimal("-500")


def test_reopening_after_close_is_allowed():
    boutique = BoutiqueFactory()
    session = services.open_session(boutique, opening_amount=Decimal("1000"))
    services.close_session(session, counted_amount=Decimal("1000"))

    new_session = services.open_session(boutique, opening_amount=Decimal("1000"))
    assert new_session.pk != session.pk
    assert CashSession.objects.filter(boutique=boutique, status=CashSession.OUVERTE).count() == 1
