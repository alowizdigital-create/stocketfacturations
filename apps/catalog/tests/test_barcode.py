"""Scan de code-barres : champ produit (unicité) et recherche exacte utilisée
par la caisse (lecteur USB/Bluetooth ou caméra)."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.catalog.forms import ProductForm
from apps.catalog.models import Product
from apps.stock import services as stock_services
from apps.stock.models import StockMovement
from apps.sync.tests.factories import BoutiqueFactory, ProductFactory
from apps.tenants.models import Membership

pytestmark = pytest.mark.django_db

URL = "catalog:product_by_barcode"


@pytest.fixture
def boutique():
    return BoutiqueFactory()


@pytest.fixture
def client_logged(client, boutique):
    from apps.accounts.models import User

    user = User.objects.create_user(email="caissier@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.CAISSIER, is_active=True)
    client.force_login(user)
    return client


def _unit(compte):
    # le nom d'une unité est unique par entreprise : on la partage
    from apps.catalog.models import Unit

    return Unit.objects.get_or_create(compte=compte, name="Pièce", defaults={"symbol": "pc"})[0]


def _product(compte, **kwargs):
    return ProductFactory(compte=compte, unit=_unit(compte), **kwargs)


def _stocked(boutique, quantity=5, **kwargs):
    product = _product(boutique.compte, **kwargs)
    if quantity:
        stock_services.apply_movement(
            boutique=boutique, product=product, type=StockMovement.ENTREE, quantity=quantity, reason="test",
        )
    return product


def _scan(client, code):
    return client.get(reverse(URL), {"code": code}).json()


# --- recherche exacte -----------------------------------------------------

def test_found_returns_the_pos_payload(client_logged, boutique):
    product = _stocked(boutique, quantity=7, barcode="6001234567890", default_sale_price=Decimal("2500"))
    data = _scan(client_logged, "6001234567890")
    assert data["status"] == "found"
    assert data["product"]["id"] == str(product.id)
    assert data["product"]["stock_qty"] == 7 and data["product"]["price"] == 2500
    # même forme que product_search : le panier JS traite les deux pareil
    search = client_logged.get(reverse("catalog:product_search"), {"q": "6001234567890"}).json()["results"][0]
    assert set(data["product"]) == set(search)


def test_match_is_exact_not_a_fragment(client_logged, boutique):
    _stocked(boutique, barcode="6001234567890")
    assert _scan(client_logged, "600123456789")["status"] == "unknown"
    assert _scan(client_logged, "1234567890")["status"] == "unknown"


def test_surrounding_whitespace_and_case_are_ignored(client_logged, boutique):
    product = _stocked(boutique, barcode="AbC-123")
    for code in ("  AbC-123\n", "abc-123", "ABC-123"):
        data = _scan(client_logged, code)
        assert data["status"] == "found" and data["product"]["id"] == str(product.id)


def test_unknown_and_empty(client_logged):
    assert _scan(client_logged, "0000000000000")["status"] == "unknown"
    assert _scan(client_logged, "")["status"] == "unknown"
    assert _scan(client_logged, "   ")["status"] == "unknown"


def test_out_of_stock_here_is_explained(client_logged, boutique):
    _stocked(boutique, quantity=0, barcode="111222333", name="Lait")
    data = _scan(client_logged, "111222333")
    assert data["status"] == "out_of_stock" and data["name"] == "Lait"


def test_stock_is_per_boutique(client_logged, boutique):
    other = BoutiqueFactory(compte=boutique.compte)
    product = _product(boutique.compte, barcode="777888999")
    stock_services.apply_movement(
        boutique=other, product=product, type=StockMovement.ENTREE, quantity=9, reason="autre boutique",
    )
    assert _scan(client_logged, "777888999")["status"] == "out_of_stock"


def test_inactive_product(client_logged, boutique):
    _stocked(boutique, barcode="555", is_active=False, name="Ancien")
    data = _scan(client_logged, "555")
    assert data["status"] == "inactive" and data["name"] == "Ancien"


def test_other_company_products_are_invisible(client_logged):
    foreign = ProductFactory(barcode="999000111")
    assert foreign.compte_id  # appartient à une autre entreprise
    assert _scan(client_logged, "999000111")["status"] == "unknown"


def test_duplicate_barcodes_are_reported_ambiguous(client_logged, boutique):
    # possible sur des données antérieures au contrôle d'unicité
    first = _stocked(boutique, barcode="DUP1", name="A")
    second = _stocked(boutique, barcode="DUP1", name="B")
    data = _scan(client_logged, "DUP1")
    assert data["status"] == "ambiguous"
    assert {r["id"] for r in data["results"]} == {str(first.id), str(second.id)}


def test_requires_login(client):
    response = client.get(reverse(URL), {"code": "1"})
    assert response.status_code == 302 and "/connexion/" in response["Location"]


# --- champ code-barres du formulaire produit ------------------------------

def _form(boutique, data, instance=None):
    payload = {"name": "Produit", "unit": _unit(boutique.compte).pk, "default_sale_price": "100", "is_active": "on"}
    payload.update(data)
    return ProductForm(payload, compte=boutique.compte, instance=instance)


def test_form_saves_and_trims_the_barcode(boutique):
    form = _form(boutique, {"barcode": "  6001234567890 \n"})
    assert form.is_valid(), form.errors
    assert form.save().barcode == "6001234567890"


def test_form_barcode_is_optional(boutique):
    form = _form(boutique, {"barcode": ""})
    assert form.is_valid() and form.save().barcode == ""


def test_two_products_without_barcode_do_not_clash(boutique):
    _product(boutique.compte, barcode="")
    assert _form(boutique, {"barcode": ""}).is_valid()


def test_form_rejects_a_barcode_already_used_in_the_company(boutique):
    _product(boutique.compte, barcode="6001234567890", name="Lait")
    form = _form(boutique, {"barcode": "6001234567890"})
    assert not form.is_valid()
    assert "Lait" in form.errors["barcode"][0]
    assert not _form(boutique, {"barcode": "6001234567890".lower()}).is_valid()


def test_form_allows_keeping_own_barcode_on_edit(boutique):
    product = _product(boutique.compte, barcode="ABC123")
    assert _form(boutique, {"barcode": "ABC123", "name": "Renommé"}, instance=product).is_valid()


def test_same_barcode_is_allowed_in_another_company(boutique):
    _product(boutique.compte, barcode="SHARED")
    other = BoutiqueFactory()
    assert _form(other, {"barcode": "SHARED"}).is_valid()


def test_barcode_is_editable_in_the_product_form_page(client_logged, boutique):
    from apps.accounts.models import User

    admin = User.objects.create_user(email="admin@test.com", password="x")
    Membership.objects.create(user=admin, boutique=boutique, role=Membership.ADMIN_COMPTE, is_active=True)
    client_logged.force_login(admin)
    _unit(boutique.compte)
    page = client_logged.get(reverse("catalog:product_create")).content.decode()
    assert 'name="barcode"' in page
    assert Product.objects.filter(compte=boutique.compte).count() == 0
