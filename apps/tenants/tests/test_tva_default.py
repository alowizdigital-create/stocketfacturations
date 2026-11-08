"""La TVA démarre à 0 ; ce que l'utilisateur règle ensuite est respecté — voir
apps.core.country_profiles, apps.tenants.forms.BoutiqueRegionalForm et
apps.catalog.views.product_create."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product, Unit
from apps.sync.tests.factories import BoutiqueFactory, ProductFactory
from apps.tenants.forms import BoutiqueRegionalForm
from apps.tenants.models import Boutique, Membership
from apps.tenants.services import create_company_with_owner

pytestmark = pytest.mark.django_db


@pytest.fixture
def boutique():
    return BoutiqueFactory()


@pytest.fixture
def admin_client(client, boutique):
    user = User.objects.create_user(email="adm@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.ADMIN_COMPTE, is_active=True)
    client.force_login(user)
    return client


def _product(boutique, rate, name="P"):
    unit, _ = Unit.objects.get_or_create(compte=boutique.compte, name="Pièce", defaults={"symbol": "pc"})
    return ProductFactory(compte=boutique.compte, unit=unit, name=name, tva_rate=Decimal(rate))


# --- départ à zéro ----------------------------------------------------------------

@pytest.mark.parametrize("country", ["CM", ""])
def test_new_company_starts_with_zero_tva(country):
    _, boutique = create_company_with_owner(
        user_model=User, entreprise_name="Ma Boite", boutique_name="B1", devise="XAF",
        email="z@test.com", password="Xk9!longpass", country=country,
    )
    assert boutique.tva_rate == Decimal("0")


def test_new_product_gets_zero_tva_by_default(admin_client, boutique):
    Unit.objects.get_or_create(compte=boutique.compte, name="Pièce", defaults={"symbol": "pc"})
    unit = Unit.objects.get(compte=boutique.compte)
    admin_client.post(reverse("catalog:product_create"), {
        "name": "Savon", "unit": str(unit.id), "default_sale_price": "1000", "is_active": "on",
        "initial_quantity": "0",
    })
    assert Product.objects.get(name="Savon").tva_rate == Decimal("0")


def test_signup_page_no_longer_announces_a_tva(client):
    html = client.get(reverse("tenants:signup")).content.decode()
    assert "TVA 19,25" not in html and "indicatif +237" in html


# --- ce que l'utilisateur modifie est pris en compte --------------------------------------

def test_user_set_tva_is_used_for_new_products(admin_client, boutique):
    boutique.tva_rate = Decimal("19.25")
    boutique.save()
    unit = Unit.objects.get_or_create(compte=boutique.compte, name="Pièce", defaults={"symbol": "pc"})[0]
    admin_client.post(reverse("catalog:product_create"), {
        "name": "Taxé", "unit": str(unit.id), "default_sale_price": "1000", "is_active": "on", "initial_quantity": "0",
    })
    assert Product.objects.get(name="Taxé").tva_rate == Decimal("19.25")


def test_lowering_boutique_tva_to_zero_updates_products_that_had_the_old_rate(boutique):
    boutique.tva_rate = Decimal("19.25")
    boutique.save()
    follows = _product(boutique, "19.25", "Suit")
    custom = _product(boutique, "5", "Perso")
    form = BoutiqueRegionalForm(
        {"devise": "XAF", "country_calling_code": "", "tva_rate": "0"}, instance=boutique,
    )
    assert form.is_valid(), form.errors
    form.save()
    follows.refresh_from_db()
    custom.refresh_from_db()
    assert follows.tva_rate == Decimal("0")
    assert custom.tva_rate == Decimal("5")          # un taux propre à un produit n'est jamais écrasé
    assert form.products_updated == 1


def test_unchanged_tva_touches_no_product(boutique):
    boutique.tva_rate = Decimal("10")
    boutique.save()
    product = _product(boutique, "10")
    form = BoutiqueRegionalForm({"devise": "XAF", "country_calling_code": "", "tva_rate": "10"}, instance=boutique)
    assert form.is_valid()
    form.save()
    assert form.products_updated == 0
    product.refresh_from_db()
    assert product.tva_rate == Decimal("10")


def test_other_companies_products_never_touched(boutique):
    boutique.tva_rate = Decimal("19.25")
    boutique.save()
    other = _product(BoutiqueFactory(), "19.25", "Autre entreprise")
    form = BoutiqueRegionalForm({"devise": "XAF", "country_calling_code": "", "tva_rate": "0"}, instance=boutique)
    assert form.is_valid()
    form.save()
    other.refresh_from_db()
    assert other.tva_rate == Decimal("19.25")


def test_settings_page_saves_tva_and_reports_updated_products(admin_client, boutique):
    boutique.tva_rate = Decimal("19.25")
    boutique.save()
    product = _product(boutique, "19.25")
    response = admin_client.post(reverse("tenants:company_settings"), {
        "form_name": "boutique", "devise": "XAF", "country_calling_code": "", "tva_rate": "0",
        "delivery_price": "", "om_account_name": "", "om_number": "", "momo_account_name": "", "momo_number": "",
    }, follow=True)
    boutique.refresh_from_db()
    product.refresh_from_db()
    assert boutique.tva_rate == Decimal("0") and product.tva_rate == Decimal("0")
    assert "TVA de 1 produit" in " ".join(str(m) for m in response.context["messages"])


def test_zero_tva_gives_prices_without_tax_on_storefront(client, boutique):
    from apps.stock import services as stock_services
    from apps.stock.models import StockMovement

    boutique.shop_enabled, boutique.shop_slug = True, "ma-boutique"
    boutique.save()
    product = _product(boutique, "0", "Chapeau")
    product.default_sale_price, product.is_online = Decimal("15000"), True
    product.save()
    stock_services.apply_movement(
        boutique=boutique, product=product, type=StockMovement.ENTREE, quantity=Decimal("5"), created_by=None,
    )
    html = client.get(reverse("storefront:home", args=["ma-boutique"])).content.decode()
    assert "15 000 " in html and "17 888" not in html
