import pytest

from apps.tenants.models import BoutiqueAPIToken

pytestmark = pytest.mark.django_db


def test_ping_without_token_is_unauthorized(anon_client):
    resp = anon_client.get("/api/v1/sync/ping/")
    assert resp.status_code == 401


def test_ping_with_invalid_token_is_unauthorized(anon_client):
    anon_client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    resp = anon_client.get("/api/v1/sync/ping/")
    assert resp.status_code == 401


def test_ping_with_inactive_token_is_unauthorized(anon_client, boutique, token_pair):
    token, raw_token = token_pair
    BoutiqueAPIToken.objects.filter(pk=token.pk).update(is_active=False)
    anon_client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_token}")
    resp = anon_client.get("/api/v1/sync/ping/")
    assert resp.status_code == 401


def test_ping_with_valid_token_succeeds_and_bumps_last_used_at(api_client, boutique, token_pair):
    token, _ = token_pair
    assert token.last_used_at is None

    resp = api_client.get("/api/v1/sync/ping/")

    assert resp.status_code == 200
    assert resp.data["boutique"]["id"] == str(boutique.id)
    token.refresh_from_db()
    assert token.last_used_at is not None
