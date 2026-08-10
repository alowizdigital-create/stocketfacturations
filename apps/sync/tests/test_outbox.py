from unittest.mock import MagicMock

import pytest
from django.test import Client as DjangoClient
from django.utils import timezone

from apps.sales.models import Client as ClientModel
from apps.sync.models import DeviceActivation, OutboxEntry, SyncCursor
from apps.sync import outbox as outbox_module

from .factories import BoutiqueFactory, MembershipFactory

pytestmark = pytest.mark.django_db


def _activate(boutique):
    DeviceActivation.objects.create(
        boutique_id=boutique.id, boutique_name=boutique.name,
        compte_id=boutique.compte_id, compte_name=boutique.compte.name,
        token="test-token",
    )


# --- enqueue() -------------------------------------------------------------


def test_enqueue_is_noop_when_online(settings):
    settings.IS_OFFLINE = False
    result = outbox_module.enqueue(OutboxEntry.CLIENT, "11111111-1111-1111-1111-111111111111")
    assert result is None
    assert OutboxEntry.objects.count() == 0


def test_enqueue_creates_entry_when_offline(settings):
    settings.IS_OFFLINE = True
    object_id = "11111111-1111-1111-1111-111111111111"
    entry = outbox_module.enqueue(OutboxEntry.CLIENT, object_id)
    assert entry is not None
    assert OutboxEntry.objects.filter(kind=OutboxEntry.CLIENT, object_id=object_id, status=OutboxEntry.PENDING).exists()


# --- Points d'appel dans les vues -------------------------------------------


def test_client_create_view_enqueues_when_offline(settings):
    settings.IS_OFFLINE = True
    boutique = BoutiqueFactory()
    membership = MembershipFactory(boutique=boutique)
    _activate(boutique)

    client = DjangoClient()
    client.force_login(membership.user)
    resp = client.post("/clients/nouveau/", {"name": "Client Offline"})

    assert resp.status_code == 302
    created = ClientModel.objects.get(name="Client Offline")
    assert OutboxEntry.objects.filter(kind=OutboxEntry.CLIENT, object_id=created.id).exists()


def test_client_create_view_does_not_enqueue_when_online():
    boutique = BoutiqueFactory()
    membership = MembershipFactory(boutique=boutique)

    client = DjangoClient()
    client.force_login(membership.user)
    # Pas de sélection explicite de boutique nécessaire : le middleware
    # l'auto-sélectionne (une seule boutique active pour cet utilisateur).
    resp = client.post("/clients/nouveau/", {"name": "Client En Ligne"})

    assert resp.status_code == 302
    assert OutboxEntry.objects.count() == 0


# --- run_push_cycle / run_pull_cycle contre un client mocké ----------------


def test_run_push_cycle_marks_sent_on_success(settings):
    settings.IS_OFFLINE = True
    boutique = BoutiqueFactory()
    client_obj = ClientModel.objects.create(boutique=boutique, name="Poussé hors-ligne")
    entry = outbox_module.enqueue(OutboxEntry.CLIENT, client_obj.id)

    mock_client = MagicMock()
    mock_client.post.return_value = {
        "idempotency_key": "k", "results": [{"id": str(client_obj.id), "status": "created"}],
    }

    summary = outbox_module.run_push_cycle(client=mock_client)

    entry.refresh_from_db()
    assert entry.status == OutboxEntry.SENT
    assert summary[OutboxEntry.CLIENT]["sent"] == 1
    mock_client.post.assert_called_once()


def test_run_push_cycle_marks_error_on_server_error_result(settings):
    settings.IS_OFFLINE = True
    boutique = BoutiqueFactory()
    client_obj = ClientModel.objects.create(boutique=boutique, name="Va échouer")
    entry = outbox_module.enqueue(OutboxEntry.CLIENT, client_obj.id)

    mock_client = MagicMock()
    mock_client.post.return_value = {
        "idempotency_key": "k",
        "results": [{"id": str(client_obj.id), "status": "error", "detail": "boom"}],
    }

    outbox_module.run_push_cycle(client=mock_client)

    entry.refresh_from_db()
    assert entry.status == OutboxEntry.ERROR
    assert entry.attempts == 1
    assert entry.last_error == "boom"


def test_run_push_cycle_leaves_pending_on_network_failure(settings):
    settings.IS_OFFLINE = True
    boutique = BoutiqueFactory()
    client_obj = ClientModel.objects.create(boutique=boutique, name="Réseau coupé")
    entry = outbox_module.enqueue(OutboxEntry.CLIENT, client_obj.id)

    mock_client = MagicMock()
    mock_client.post.side_effect = ConnectionError("no network")

    outbox_module.run_push_cycle(client=mock_client)

    entry.refresh_from_db()
    assert entry.status == OutboxEntry.PENDING


def test_run_pull_cycle_upserts_and_advances_cursor(settings):
    from apps.sync import pull as pull_module

    settings.IS_OFFLINE = True
    boutique = BoutiqueFactory()
    _activate(boutique)
    server_time = timezone.now().isoformat()

    mock_client = MagicMock()

    def fake_get(path, params=None):
        if path == "pull/tenants/boutique/":
            return {
                "id": str(boutique.id), "name": boutique.name, "code": boutique.code,
                "devise": boutique.devise, "compte_id": str(boutique.compte_id),
                "compte_name": boutique.compte.name, "server_time": server_time,
            }
        return {"results": [], "next": None, "server_time": server_time}

    mock_client.get.side_effect = fake_get

    summary = pull_module.run_pull_cycle(client=mock_client)

    assert summary["boutique"] == 1
    assert SyncCursor.objects.filter(resource="tax_rates").exists()
    cursor = SyncCursor.objects.get(resource="tax_rates")
    assert cursor.last_synced_at is not None
