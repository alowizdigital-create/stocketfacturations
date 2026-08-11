import pytest

from apps.sales import services as sales_services
from apps.sales.models import Invoice

pytestmark = pytest.mark.django_db

INVOICES_URL = "/api/v1/sync/pull/sales/invoices/"
SALES_URL = "/api/v1/sync/pull/sales/sales/"


def _invoice_with_payment(boutique, product):
    invoice = sales_services.build_invoice(
        boutique=boutique,
        client=None,
        type=Invoice.FACTURE,
        created_by=None,
        lines_data=[{
            "product": product, "description": product.name, "quantity": 2,
            "unit_price_ht": 1000, "tva_rate": 18,
        }],
    )
    sales_services.record_payment(invoice, amount=2360, method="ESPECES")
    return invoice


def test_pull_invoices_includes_nested_lines_and_payments(api_client, boutique, product):
    invoice = _invoice_with_payment(boutique, product)

    resp = api_client.get(INVOICES_URL)

    assert resp.status_code == 200
    ids = {item["id"]: item for item in resp.data["results"]}
    assert str(invoice.id) in ids
    payload = ids[str(invoice.id)]
    assert payload["status"] == Invoice.PAYEE
    assert len(payload["lines"]) == 1
    assert payload["lines"][0]["description"] == product.name
    assert len(payload["payments"]) == 1
    assert payload["payments"][0]["amount"] == "2360"


def test_pull_invoices_only_returns_own_boutique(api_client, boutique, product):
    from .factories import BoutiqueFactory, ProductFactory

    other_boutique = BoutiqueFactory()
    other_product = ProductFactory(compte=other_boutique.compte)
    other_invoice = _invoice_with_payment(other_boutique, other_product)

    resp = api_client.get(INVOICES_URL)

    ids = {item["id"] for item in resp.data["results"]}
    assert str(other_invoice.id) not in ids


def test_pull_sales_includes_nested_lines_and_links_invoice(api_client, boutique, product):
    from apps.stock.models import StockMovement
    from apps.stock.services import apply_movement

    apply_movement(boutique=boutique, product=product, type=StockMovement.ENTREE, quantity=10, reason="stock initial")

    sale = sales_services.build_sale(
        boutique=boutique, client=None, created_by=None,
        lines_data=[{
            "product": product, "description": product.name, "quantity": 1,
            "unit_price_ht": 1000, "tva_rate": 18,
        }],
    )
    sale = sales_services.confirm_sale(sale)
    invoice = sales_services.generate_invoice_from_sale(sale)

    resp = api_client.get(SALES_URL)

    ids = {item["id"]: item for item in resp.data["results"]}
    assert str(sale.id) in ids
    payload = ids[str(sale.id)]
    assert str(payload["invoice_id"]) == str(invoice.id)
    assert len(payload["lines"]) == 1
    assert str(payload["lines"][0]["product_id"]) == str(product.id)
