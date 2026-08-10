import uuid

import pytest

from apps.sales.models import Client

pytestmark = pytest.mark.django_db

URL = "/api/v1/sync/push/clients/"


def test_push_client_creates_then_replays_as_duplicate(api_client, boutique):
    client_id = str(uuid.uuid4())
    item = {"id": client_id, "name": "Client Hors-Ligne", "phone": "0700000000"}

    first = api_client.post(URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="c-1")
    assert first.data["results"][0]["status"] == "created"

    second = api_client.post(URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="c-2")
    assert second.data["results"][0]["status"] == "duplicate"

    assert Client.objects.filter(pk=client_id, boutique=boutique).count() == 1


def test_push_clients_partial_failure_does_not_block_batch(api_client, boutique):
    good_item = {"id": str(uuid.uuid4()), "name": "Client Valide"}
    bad_item = {"id": str(uuid.uuid4())}  # `name` manquant

    resp = api_client.post(
        URL, {"items": [bad_item, good_item]}, format="json", HTTP_IDEMPOTENCY_KEY="c-3",
    )

    assert resp.status_code == 200
    results = {r["id"]: r["status"] for r in resp.data["results"]}
    assert results[good_item["id"]] == "created"
    assert results[bad_item["id"]] == "error"
    assert Client.objects.filter(pk=good_item["id"]).exists()
