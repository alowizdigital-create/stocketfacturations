import pytest
from django.utils import timezone

from .factories import ProductFactory

pytestmark = pytest.mark.django_db

URL = "/api/v1/sync/pull/catalog/products/"


def test_pull_products_returns_boutique_compte_catalog(api_client, boutique, product):
    resp = api_client.get(URL)

    assert resp.status_code == 200
    ids = {item["id"] for item in resp.data["results"]}
    assert str(product.id) in ids
    assert "server_time" in resp.data


def test_pull_products_since_filters_out_older_rows(api_client, boutique, product):
    future = (timezone.now() + timezone.timedelta(days=1)).isoformat()

    resp = api_client.get(URL, {"since": future})

    assert resp.status_code == 200
    assert resp.data["results"] == []


def test_pull_products_only_returns_own_compte(api_client, boutique, product):
    other_product = ProductFactory()  # autre compte

    resp = api_client.get(URL)

    ids = {item["id"] for item in resp.data["results"]}
    assert str(product.id) in ids
    assert str(other_product.id) not in ids


def test_pull_products_invalid_since_is_rejected(api_client, boutique):
    resp = api_client.get(URL, {"since": "not-a-date"})
    assert resp.status_code == 400
