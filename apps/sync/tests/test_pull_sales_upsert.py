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


def test_upsert_invoice_number_collision_is_skipped_without_crashing_other_items():
    """Régression réelle observée en production : deux factures tirées
    dans la même page qui finissent avec le même (boutique, number) —
    l'update_or_create() de la seconde lève IntegrityError. Avant le
    correctif, cette exception remontait telle quelle jusqu'à
    desktop.sync_worker, qui exécutait pull puis push dans le MÊME bloc
    try/except : le push n'était donc plus jamais atteint, tant que cette
    facture restait en conflit — plus aucune donnée locale ne partait,
    silencieusement, à chaque cycle. _upsert_invoice doit maintenant
    absorber l'erreur sur CET item et continuer avec les autres."""
    boutique = BoutiqueFactory()
    existing_id = str(uuid.uuid4())
    colliding_id = str(uuid.uuid4())
    other_id = str(uuid.uuid4())

    Invoice.objects.create(boutique=boutique, id=existing_id, number="BTQ-DUP-0001", total_ttc=1000)

    colliding_item = {
        # Même numéro que la facture déjà locale, mais un id différent :
        # l'update_or_create(id=colliding_id, defaults={"number": ...})
        # crée une nouvelle ligne, qui viole la contrainte unique.
        "id": colliding_id, "client_id": None, "number": "BTQ-DUP-0001",
        "type": Invoice.FACTURE, "status": Invoice.VALIDEE, "issue_date": "2026-01-01",
        "due_date": None, "currency": "XOF", "subtotal_ht": "1000", "total_tva": "180",
        "total_ttc": "1180", "discount_amount": "0", "created_by_id": None,
        "lines": [], "payments": [],
    }
    other_item = {
        "id": other_id, "client_id": None, "number": "BTQ-OK-0002",
        "type": Invoice.FACTURE, "status": Invoice.VALIDEE, "issue_date": "2026-01-01",
        "due_date": None, "currency": "XOF", "subtotal_ht": "500", "total_tva": "90",
        "total_ttc": "590", "discount_amount": "0", "created_by_id": None,
        "lines": [], "payments": [],
    }

    pull_module._upsert_invoice([colliding_item, other_item], boutique.id)

    assert not Invoice.objects.filter(pk=colliding_id).exists()
    assert Invoice.objects.filter(pk=other_id, number="BTQ-OK-0002").exists()


def test_run_pull_cycle_continues_after_one_resource_fails(monkeypatch):
    boutique = BoutiqueFactory()
    _activate(boutique)

    def failing_upsert(items, scope_id):
        raise RuntimeError("upsert cassé pour cette ressource")

    # PULL_RESOURCES capture la fonction _upsert_category par référence
    # directe à l'import : patcher l'attribut du module ne suffit pas, il
    # faut remplacer l'entrée correspondante dans la liste elle-même.
    patched_resources = [
        (resource, path, failing_upsert if resource == "categories" else upsert_fn, scope)
        for resource, path, upsert_fn, scope in pull_module.PULL_RESOURCES
    ]
    monkeypatch.setattr(pull_module, "PULL_RESOURCES", patched_resources)

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

    summary = pull_module.run_pull_cycle(client=mock_client)  # ne doit jamais lever

    assert summary["categories"] == "error"
    assert summary["units"] == 0  # les ressources suivantes sont bien traitées malgré tout
    called_paths = [call.args[0] for call in mock_client.get.call_args_list]
    assert "pull/catalog/units/" in called_paths


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
