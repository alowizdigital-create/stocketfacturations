import uuid

import pytest

from apps.catalog.models import Category, Product, Unit

pytestmark = pytest.mark.django_db

CATEGORIES_URL = "/api/v1/sync/push/catalog/categories/"
UNITS_URL = "/api/v1/sync/push/catalog/units/"
PRODUCTS_URL = "/api/v1/sync/push/catalog/products/"


def test_push_category_creates_then_update_modifies_in_place(api_client, boutique):
    category_id = str(uuid.uuid4())
    item = {"id": category_id, "name": "Boissons"}

    first = api_client.post(CATEGORIES_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="cat-1")
    assert first.data["results"][0]["status"] == "created"

    item["name"] = "Boissons fraîches"
    second = api_client.post(CATEGORIES_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="cat-2")
    assert second.data["results"][0]["status"] == "duplicate"

    category = Category.objects.get(pk=category_id, compte=boutique.compte)
    assert category.name == "Boissons fraîches"
    assert Category.objects.filter(compte=boutique.compte).count() == 1


def test_push_unit_creates_then_update_modifies_in_place(api_client, boutique):
    unit_id = str(uuid.uuid4())
    item = {"id": unit_id, "name": "Carton", "symbol": "ctn"}

    api_client.post(UNITS_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="unit-1")

    item["symbol"] = "CTN"
    resp = api_client.post(UNITS_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="unit-2")
    assert resp.data["results"][0]["status"] == "duplicate"

    unit = Unit.objects.get(pk=unit_id, compte=boutique.compte)
    assert unit.symbol == "CTN"


def test_push_product_creates_then_update_modifies_price(api_client, boutique, product):
    unit_id = str(product.unit_id)
    product_id = str(uuid.uuid4())
    item = {
        "id": product_id, "name": "Sac de riz 25kg", "unit_id": unit_id,
        "default_sale_price": "15000",
    }

    first = api_client.post(PRODUCTS_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="prod-1")
    assert first.data["results"][0]["status"] == "created"

    item["default_sale_price"] = "16000"
    second = api_client.post(PRODUCTS_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="prod-2")
    assert second.data["results"][0]["status"] == "duplicate"

    created = Product.objects.get(pk=product_id, compte=boutique.compte)
    assert created.default_sale_price == 16000


def test_push_product_with_unknown_unit_is_reported_as_error(api_client, boutique):
    item = {
        "id": str(uuid.uuid4()), "name": "Produit orphelin",
        "unit_id": str(uuid.uuid4()), "default_sale_price": "1000",
    }

    resp = api_client.post(PRODUCTS_URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="prod-3")

    assert resp.data["results"][0]["status"] == "error"
    assert not Product.objects.filter(pk=item["id"]).exists()
