import uuid

import pytest

from apps.sales.models import Invoice, Payment, Sale
from apps.stock.models import StockLevel, StockMovement
from apps.stock.services import apply_movement

pytestmark = pytest.mark.django_db

URL = "/api/v1/sync/push/sale-transactions/"


def _bundle(product, sale_id=None, movement_id=None, invoice_id=None, payment_id=None):
    return {
        "id": str(sale_id or uuid.uuid4()),
        "lines": [
            {
                "product_id": str(product.id),
                "description": product.name,
                "quantity": "2",
                "unit_price_ht": "1000",
                "tva_rate": "18",
                "stock_movement_id": str(movement_id or uuid.uuid4()),
            }
        ],
        "generate_invoice": True,
        "invoice_id": str(invoice_id or uuid.uuid4()),
        "payment": {
            "id": str(payment_id or uuid.uuid4()),
            "amount": "2360",
            "method": "ESPECES",
        },
    }


def _initial_stock(boutique, product, quantity=50):
    apply_movement(
        boutique=boutique, product=product, type=StockMovement.ENTREE,
        quantity=quantity, reason="stock initial de test",
    )


def test_sale_transaction_creates_full_chain(api_client, boutique, product):
    _initial_stock(boutique, product)
    item = _bundle(product)

    resp = api_client.post(
        URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="key-1",
    )

    assert resp.status_code == 200
    result = resp.data["results"][0]
    assert result["status"] == "created"
    assert result["invoice_id"] is not None

    assert Sale.objects.filter(pk=item["id"]).exists()
    invoice = Invoice.objects.get(pk=item["invoice_id"])
    assert invoice.status == Invoice.PAYEE
    assert Payment.objects.filter(pk=item["payment"]["id"]).exists()

    level = StockLevel.objects.get(boutique=boutique, product=product)
    assert level.quantity == 48  # 50 - 2


def test_replaying_same_idempotency_key_returns_cached_response_without_side_effects(
    api_client, boutique, product,
):
    _initial_stock(boutique, product)
    item = _bundle(product)
    payload = {"items": [item]}

    first = api_client.post(URL, payload, format="json", HTTP_IDEMPOTENCY_KEY="key-2")
    second = api_client.post(URL, payload, format="json", HTTP_IDEMPOTENCY_KEY="key-2")

    assert first.data == second.data
    assert Sale.objects.filter(pk=item["id"]).count() == 1
    assert StockMovement.objects.filter(pk=item["lines"][0]["stock_movement_id"]).count() == 1

    level = StockLevel.objects.get(boutique=boutique, product=product)
    assert level.quantity == 48  # décrémenté une seule fois


def test_replaying_same_sale_id_under_different_key_reports_duplicate(api_client, boutique, product):
    _initial_stock(boutique, product)
    item = _bundle(product)

    first = api_client.post(URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="key-3a")
    assert first.data["results"][0]["status"] == "created"

    second = api_client.post(URL, {"items": [item]}, format="json", HTTP_IDEMPOTENCY_KEY="key-3b")
    assert second.data["results"][0]["status"] == "duplicate"

    assert Sale.objects.filter(pk=item["id"]).count() == 1
    level = StockLevel.objects.get(boutique=boutique, product=product)
    assert level.quantity == 48


def test_same_key_different_body_is_conflict(api_client, boutique, product):
    _initial_stock(boutique, product)
    item_a = _bundle(product)
    item_b = _bundle(product)

    api_client.post(URL, {"items": [item_a]}, format="json", HTTP_IDEMPOTENCY_KEY="key-4")
    resp = api_client.post(URL, {"items": [item_b]}, format="json", HTTP_IDEMPOTENCY_KEY="key-4")

    assert resp.status_code == 409


def test_missing_idempotency_key_is_rejected(api_client, boutique, product):
    item = _bundle(product)
    resp = api_client.post(URL, {"items": [item]}, format="json")
    assert resp.status_code == 400


def test_one_bad_item_does_not_block_the_rest_of_the_batch(api_client, boutique, product):
    _initial_stock(boutique, product)
    good_item = _bundle(product)
    bad_item = _bundle(product)
    bad_item["lines"][0]["product_id"] = str(uuid.uuid4())  # produit inexistant

    resp = api_client.post(
        URL, {"items": [bad_item, good_item]}, format="json", HTTP_IDEMPOTENCY_KEY="key-5",
    )

    assert resp.status_code == 200
    statuses = {r["id"]: r["status"] for r in resp.data["results"]}
    assert statuses[good_item["id"]] == "created"
    assert Sale.objects.filter(pk=good_item["id"]).exists()
