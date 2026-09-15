import uuid
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.cashier import services as cashier_services
from apps.cashier.models import CashMovement, CashSession
from apps.sync import pull as pull_module

pytestmark = pytest.mark.django_db

PUSH_SESSIONS_URL = "/api/v1/sync/push/cash-sessions/"
PUSH_MOVEMENTS_URL = "/api/v1/sync/push/cash-movements/"


# --- Push (offline -> online) -----------------------------------------------


def test_push_cash_session_open_then_close(api_client, boutique):
    session_id = str(uuid.uuid4())
    open_item = {
        "id": session_id, "status": CashSession.OUVERTE,
        "opening_amount": "5000", "opened_at": timezone.now().isoformat(),
    }
    resp = api_client.post(PUSH_SESSIONS_URL, {"items": [open_item]}, format="json", HTTP_IDEMPOTENCY_KEY="s-1")
    assert resp.data["results"][0]["status"] == "created"

    session = CashSession.objects.get(pk=session_id)
    assert session.status == CashSession.OUVERTE
    assert session.opening_amount == Decimal("5000")

    close_item = {
        "id": session_id, "status": CashSession.FERMEE,
        "opening_amount": "5000", "counted_amount": "4800", "closing_note": "Écart",
    }
    resp2 = api_client.post(PUSH_SESSIONS_URL, {"items": [close_item]}, format="json", HTTP_IDEMPOTENCY_KEY="s-2")
    assert resp2.data["results"][0]["status"] == "duplicate"

    session.refresh_from_db()
    assert session.status == CashSession.FERMEE
    assert session.counted_amount == Decimal("4800")
    assert CashSession.objects.filter(boutique=boutique).count() == 1


def test_push_cash_movement_creates_then_replays_as_duplicate(api_client, boutique):
    session = cashier_services.open_session(boutique, opening_amount=Decimal("1000"))
    movement_id = str(uuid.uuid4())
    item = {
        "id": movement_id, "session_id": str(session.id),
        "type": CashMovement.SORTIE, "amount": "200", "reason": "Course",
    }
    first = api_client.post(PUSH_MOVEMENTS_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="m-1")
    assert first.data["results"][0]["status"] == "created"

    second = api_client.post(PUSH_MOVEMENTS_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="m-2")
    assert second.data["results"][0]["status"] == "duplicate"

    assert CashMovement.objects.filter(pk=movement_id, boutique=boutique).count() == 1


def test_push_cash_movement_unknown_session_is_reported_as_error(api_client, boutique):
    item = {
        "id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()),
        "type": CashMovement.ENTREE, "amount": "100", "reason": "Test",
    }
    resp = api_client.post(PUSH_MOVEMENTS_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="m-err")
    assert resp.data["results"][0]["status"] == "error"
    assert CashMovement.objects.count() == 0


# --- Pull (online -> offline) ------------------------------------------------


def test_upsert_cash_session_creates_and_updates(boutique):
    item = {
        "id": str(uuid.uuid4()), "number": "BTQ-001-CAISSE-20260101-0001",
        "status": CashSession.OUVERTE, "opening_amount": "3000",
        "opened_at": timezone.now().isoformat(),
    }
    pull_module._upsert_cash_session([item], boutique.id)
    session = CashSession.objects.get(pk=item["id"])
    assert session.opening_amount == Decimal("3000")

    item["status"] = CashSession.FERMEE
    item["counted_amount"] = "2900"
    pull_module._upsert_cash_session([item], boutique.id)
    session.refresh_from_db()
    assert session.status == CashSession.FERMEE
    assert session.counted_amount == Decimal("2900")


def test_upsert_cash_movement_creates(boutique):
    session = cashier_services.open_session(boutique, opening_amount=Decimal("0"))
    item = {
        "id": str(uuid.uuid4()), "session_id": str(session.id),
        "type": CashMovement.ENTREE, "amount": "150", "reason": "Appoint",
    }
    pull_module._upsert_cash_movement([item], boutique.id)
    movement = CashMovement.objects.get(pk=item["id"])
    assert movement.amount == Decimal("150")
    assert movement.session_id == session.id


def test_upsert_cash_movement_skips_when_session_unresolved(boutique):
    item = {
        "id": str(uuid.uuid4()), "session_id": str(uuid.uuid4()),
        "type": CashMovement.ENTREE, "amount": "150", "reason": "Appoint",
    }
    pull_module._upsert_cash_movement([item], boutique.id)  # ne doit jamais lever
    assert CashMovement.objects.count() == 0
