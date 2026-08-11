import uuid
from unittest.mock import MagicMock

import pytest

from apps.sales.models import Invoice, InvoiceLine, Payment, Sale, SaleLine
from apps.sync import pull as pull_module
from apps.sync.models import DeviceActivation

from .factories import BoutiqueFactory

pytestmark = pytest.mark.django_db


def _activate(boutique):
    DeviceActivation.objects.create(
        boutique_id=boutique.id, boutique_name=boutique.name,
        compte_id=boutique.compte_id, compte_name=boutique.compte.name,
        token="test-token",
    )


def _resource_response(path, item, since_used):
    return {"results": [item] if not since_used else [], "next": None, "server_time": "2026-01-01T00:00:00Z"}


def test_upsert_invoice_creates_lines_and_payments_and_ignores_unknown_product():
    boutique = BoutiqueFactory()
    invoice_id = str(uuid.uuid4())
    line_id = str(uuid.uuid4())
    payment_id = str(uuid.uuid4())
    unknown_product_id = str(uuid.uuid4())

    item = {
        "id": invoice_id, "client_id": None, "number": "BTQ-20260101-0001",
        "type": Invoice.FACTURE, "status": Invoice.PAYEE, "issue_date": "2026-01-01",
        "due_date": None, "currency": "XOF", "subtotal_ht": "2000", "total_tva": "360",
        "total_ttc": "2360", "discount_amount": "0", "created_by_id": None,
        "lines": [{
            "id": line_id, "product_id": unknown_product_id, "description": "Article",
            "quantity": "2", "unit_price_ht": "1000", "tva_rate": "18",
            "discount_amount": "0", "line_total_ht": "2000", "line_total_ttc": "2360",
            "position": 0,
        }],
        "payments": [{
            "id": payment_id, "amount": "2360", "method": "ESPECES",
            "reference": "", "paid_at": "2026-01-01T10:00:00Z", "created_by_id": None,
        }],
    }

    pull_module._upsert_invoice([item], boutique.id)

    invoice = Invoice.objects.get(pk=invoice_id)
    assert invoice.boutique_id == boutique.id
    assert invoice.status == Invoice.PAYEE

    line = InvoiceLine.objects.get(pk=line_id)
    assert line.invoice_id == invoice.id
    assert line.product_id is None  # produit inconnu localement -> résolution défensive

    payment = Payment.objects.get(pk=payment_id)
    assert payment.invoice_id == invoice.id
    assert payment.amount == 2360


def test_upsert_sale_links_known_invoice_and_ignores_unknown_invoice():
    boutique = BoutiqueFactory()
    known_invoice = Invoice.objects.create(boutique=boutique, number="BTQ-1", total_ttc=1000)

    sale_a_id = str(uuid.uuid4())
    line_a_id = str(uuid.uuid4())
    sale_b_id = str(uuid.uuid4())
    line_b_id = str(uuid.uuid4())
    unknown_invoice_id = str(uuid.uuid4())

    item_linked = {
        "id": sale_a_id, "client_id": None, "number": "BTQ-V-1", "status": "CONFIRMEE",
        "sale_date": "2026-01-01", "currency": "XOF", "subtotal_ht": "1000",
        "total_tva": "180", "total_ttc": "1180", "invoice_id": str(known_invoice.id),
        "created_by_id": None,
        "lines": [{
            "id": line_a_id, "product_id": None, "description": "Article",
            "quantity": "1", "unit_price_ht": "1000", "tva_rate": "18",
            "line_total_ht": "1000", "line_total_ttc": "1180", "position": 0,
        }],
    }
    item_orphan = {
        "id": sale_b_id, "client_id": None, "number": "BTQ-V-2", "status": "CONFIRMEE",
        "sale_date": "2026-01-01", "currency": "XOF", "subtotal_ht": "1000",
        "total_tva": "180", "total_ttc": "1180", "invoice_id": unknown_invoice_id,
        "created_by_id": None,
        "lines": [{
            "id": line_b_id, "product_id": None, "description": "Article",
            "quantity": "1", "unit_price_ht": "1000", "tva_rate": "18",
            "line_total_ht": "1000", "line_total_ttc": "1180", "position": 0,
        }],
    }

    pull_module._upsert_sale([item_linked, item_orphan], boutique.id)

    sale_a = Sale.objects.get(pk=sale_a_id)
    assert sale_a.invoice_id == known_invoice.id
    assert SaleLine.objects.filter(pk=line_a_id, sale=sale_a).exists()

    sale_b = Sale.objects.get(pk=sale_b_id)
    assert sale_b.invoice_id is None  # facture inconnue localement -> résolution défensive


def test_run_pull_cycle_pulls_invoices_and_sales_resources():
    boutique = BoutiqueFactory()
    _activate(boutique)

    mock_client = MagicMock()

    def fake_get(path, params=None):
        if path == "pull/tenants/boutique/":
            return {
                "id": str(boutique.id), "name": boutique.name, "code": boutique.code,
                "devise": boutique.devise, "compte_id": str(boutique.compte_id),
                "compte_name": boutique.compte.name, "server_time": "2026-01-01T00:00:00Z",
            }
        return {"results": [], "next": None, "server_time": "2026-01-01T00:00:00Z"}

    mock_client.get.side_effect = fake_get

    summary = pull_module.run_pull_cycle(client=mock_client)

    assert "invoices" in summary
    assert "sales" in summary
    called_paths = [call.args[0] for call in mock_client.get.call_args_list]
    assert "pull/sales/invoices/" in called_paths
    assert "pull/sales/sales/" in called_paths
