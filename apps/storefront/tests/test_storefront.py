"""Boutique en ligne : vitrine publique, panier, commande (livraison / Mobile
Money manuel), réglages vendeur — voir apps.storefront."""

from decimal import Decimal

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Unit
from apps.core.models import Notification
from apps.sales.models import Client, Invoice
from apps.stock import services as stock_services
from apps.stock.models import StockMovement
from apps.sync.tests.factories import BoutiqueFactory, ProductFactory
from apps.tenants.models import Boutique, Membership

pytestmark = pytest.mark.django_db

SLUG = "ma-boutique"


@pytest.fixture
def boutique():
    b = BoutiqueFactory()
    b.shop_enabled, b.shop_slug, b.devise = True, SLUG, "XAF"
    b.phone = "677000000"
    b.save()
    return b


def _product(boutique, name="Savon", price="500", stock=10, online=True, **kwargs):
    unit, _ = Unit.objects.get_or_create(compte=boutique.compte, name="Pièce", defaults={"symbol": "pc"})
    kwargs.setdefault("tva_rate", Decimal("0"))
    product = ProductFactory(compte=boutique.compte, unit=unit, name=name, default_sale_price=Decimal(price),
                             is_online=online, **kwargs)
    if stock:
        stock_services.apply_movement(
            boutique=boutique, product=product, type=StockMovement.ENTREE, quantity=Decimal(stock), created_by=None,
        )
    return product


def _url(name, *args):
    return reverse(f"storefront:{name}", args=[SLUG, *args])


FORM = {"name": "Awa", "phone": "677123456", "address": "Akwa, Douala", "payment_mode": "LIVRAISON"}


# --- accès ----------------------------------------------------------------------

def test_unknown_or_disabled_shop_is_404(client, boutique):
    assert client.get(reverse("storefront:home", args=["inconnue"])).status_code == 404
    boutique.shop_enabled = False
    boutique.save()
    assert client.get(_url("home")).status_code == 404


@override_settings(IS_OFFLINE=True)
def test_shop_not_served_offline(client, boutique):
    # sans activation, le poste redirige vers l'écran d'activation ; activé,
    # la vue elle-même répond 404 — jamais 200.
    assert client.get(_url("home")).status_code in (302, 404)


def test_shop_hidden_when_subscription_expired(client, boutique):
    from datetime import timedelta

    from django.utils import timezone

    from apps.tenants.models import Plan, Subscription

    plan = Plan.objects.create(name="T", max_boutiques=1, max_users=1)
    Subscription.objects.create(compte=boutique.compte, plan=plan, expires_at=timezone.localdate() - timedelta(days=3))
    assert client.get(_url("home")).status_code == 404


# --- catalogue --------------------------------------------------------------------

def test_home_lists_only_published_active_products(client, boutique):
    _product(boutique, "Visible")
    _product(boutique, "Cache", online=False)
    _product(boutique, "Inactif", is_active=False)
    html = client.get(_url("home")).content.decode()
    assert "Visible" in html and "Cache" not in html and "Inactif" not in html


def test_other_company_products_not_shown(client, boutique):
    other = BoutiqueFactory()
    ProductFactory(compte=other.compte, name="Autre", is_online=True)
    assert "Autre" not in client.get(_url("home")).content.decode()


def test_search_and_soldout_badge(client, boutique):
    _product(boutique, "Savon")
    _product(boutique, "Epuise", stock=0)
    html = client.get(_url("home"), {"q": "Epuise"}).content.decode()
    assert "Epuise" in html and "Savon" not in html and "Épuisé" in html


def test_price_shown_ttc(client, boutique):
    _product(boutique, "Taxe", price="1000", tva_rate=Decimal("19.25"))
    assert "192 XAF" in client.get(_url("home")).content.decode()  # 1000 * 1,1925 = 1192,5, arrondi comme sur les factures


def test_product_page(client, boutique):
    p = _product(boutique)
    assert client.get(_url("product", p.id)).status_code == 200
    hidden = _product(boutique, "Cache", online=False)
    assert client.get(_url("product", hidden.id)).status_code == 404


# --- panier -----------------------------------------------------------------------

def test_cart_add_clamps_to_stock_and_updates(client, boutique):
    p = _product(boutique, stock=3)
    client.post(_url("cart_add", p.id), {"quantity": 5})
    html = client.get(_url("cart")).content.decode()
    assert 'value="3"' in html
    client.post(_url("cart_update", p.id), {"quantity": 2})
    assert 'value="2"' in client.get(_url("cart")).content.decode()
    client.post(_url("cart_update", p.id), {"quantity": 0})
    assert "Votre panier est vide" in client.get(_url("cart")).content.decode()


def test_cannot_add_soldout_product(client, boutique):
    p = _product(boutique, stock=0)
    client.post(_url("cart_add", p.id), {"quantity": 1})
    assert "Votre panier est vide" in client.get(_url("cart")).content.decode()


def test_cart_is_per_shop_session(client, boutique):
    p = _product(boutique)
    client.post(_url("cart_add", p.id), {"quantity": 1})
    from django.test import Client as C

    assert "Votre panier est vide" in C().get(_url("cart")).content.decode()


# --- commande ---------------------------------------------------------------------

def _fill_cart(client, boutique, qty=2, **kwargs):
    p = _product(boutique, **kwargs)
    client.post(_url("cart_add", p.id), {"quantity": qty})
    return p


def test_checkout_redirects_when_cart_empty(client, boutique):
    assert client.get(_url("checkout")).status_code == 302


def test_place_order_cash_on_delivery(client, boutique):
    boutique.shop_delivery_fee = Decimal("1000")
    boutique.save()
    p = _fill_cart(client, boutique, qty=2)
    manager = User.objects.create_user(email="g@test.com", password="x")
    Membership.objects.create(user=manager, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)

    response = client.post(_url("checkout"), FORM)
    assert response.status_code == 302
    commande = Invoice.objects.get(boutique=boutique)
    assert response.url == _url("confirmation", commande.id)
    assert commande.type == Invoice.COMMANDE and commande.source == Invoice.EN_LIGNE
    assert commande.delivery_status == Invoice.EN_ATTENTE and commande.status == Invoice.BROUILLON
    assert commande.delivery_address == "Akwa, Douala" and commande.online_payment_mode == "LIVRAISON"
    assert commande.total_ttc == Decimal("2000")  # 2 x 500 + 1000 de livraison
    assert commande.lines.count() == 2 and commande.client.phone == "677123456"
    # le stock n'est déduit qu'à la livraison
    from apps.stock.models import StockLevel

    assert StockLevel.objects.get(boutique=boutique, product=p).quantity == Decimal("10")
    assert Notification.objects.filter(user=manager, message__contains="commande en ligne").exists()
    assert "Votre panier est vide" in client.get(_url("cart")).content.decode()
    assert client.get(response.url).status_code == 200


def test_returning_customer_reuses_client(client, boutique):
    _fill_cart(client, boutique)
    client.post(_url("checkout"), FORM)
    assert Client.objects.filter(boutique=boutique).count() == 1
    from django.utils import timezone

    session = client.session
    session["shop_last_order_at"] = timezone.now().timestamp() - 600
    session.save()
    p = _product(boutique, "Autre")
    client.post(_url("cart_add", p.id), {"quantity": 1})
    client.post(_url("checkout"), FORM)
    assert Client.objects.filter(boutique=boutique).count() == 1 and Invoice.objects.count() == 2


def test_mobile_money_requires_reference_and_numbers(client, boutique):
    boutique.om_number = "699000111"
    boutique.save()
    _fill_cart(client, boutique)
    bad = client.post(_url("checkout"), {**FORM, "payment_mode": "MOBILE_MONEY"})
    assert bad.status_code == 200 and not Invoice.objects.exists()
    ok = client.post(_url("checkout"), {**FORM, "payment_mode": "MOBILE_MONEY", "payment_reference": "TX123"})
    assert ok.status_code == 302
    commande = Invoice.objects.get()
    assert commande.online_payment_mode == "MOBILE_MONEY" and commande.online_payment_reference == "TX123"


def test_mobile_money_not_offered_without_numbers(client, boutique):
    _fill_cart(client, boutique)
    response = client.post(_url("checkout"), {**FORM, "payment_mode": "MOBILE_MONEY", "payment_reference": "X"})
    assert response.status_code == 200 and not Invoice.objects.exists()


def test_honeypot_and_bad_phone_rejected(client, boutique):
    _fill_cart(client, boutique)
    assert client.post(_url("checkout"), {**FORM, "website": "http://spam"}).status_code == 200
    assert client.post(_url("checkout"), {**FORM, "phone": "12"}).status_code == 200
    assert not Invoice.objects.exists()


def test_order_blocked_when_stock_dropped(client, boutique):
    p = _fill_cart(client, boutique, qty=5, stock=5)
    stock_services.apply_movement(
        boutique=boutique, product=p, type=StockMovement.AJUSTEMENT, quantity=Decimal("-3"), created_by=None,
    )
    response = client.post(_url("checkout"), FORM)
    assert response.status_code == 200 and not Invoice.objects.exists()


def test_cooldown_blocks_double_submit(client, boutique):
    _fill_cart(client, boutique)
    client.post(_url("checkout"), FORM)
    p = _product(boutique, "Autre")
    client.post(_url("cart_add", p.id), {"quantity": 1})
    client.post(_url("checkout"), FORM)
    assert Invoice.objects.count() == 1


def test_confirmation_scoped_to_shop_and_online_orders(client, boutique):
    _fill_cart(client, boutique)
    commande = Invoice.objects.get(pk=client.post(_url("checkout"), FORM).url.rstrip("/").split("/")[-1])
    other = BoutiqueFactory()
    other.shop_enabled, other.shop_slug = True, "autre"
    other.save()
    assert client.get(reverse("storefront:confirmation", args=["autre", commande.id])).status_code == 404


# --- côté vendeur -----------------------------------------------------------------

@pytest.fixture
def admin_client(client, boutique):
    user = User.objects.create_user(email="a@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.ADMIN_COMPTE, is_active=True)
    client.force_login(user)
    return client


def test_shop_settings_enable_and_slug_unique(admin_client, boutique):
    BoutiqueFactory().__class__.objects.filter(pk=BoutiqueFactory().pk).update(shop_slug="pris")
    url = reverse("tenants:shop_settings")
    bad = admin_client.post(url, {"shop_enabled": "on", "shop_slug": "pris", "shop_delivery_fee": "0"})
    assert bad.status_code == 200
    ok = admin_client.post(url, {"shop_enabled": "on", "shop_slug": "nouveau", "shop_delivery_fee": "1500",
                                 "shop_description": "Bienvenue"})
    assert ok.status_code == 302
    boutique.refresh_from_db()
    assert boutique.shop_slug == "nouveau" and boutique.shop_delivery_fee == Decimal("1500")


def test_shop_settings_requires_slug_to_enable(admin_client, boutique):
    response = admin_client.post(reverse("tenants:shop_settings"), {"shop_enabled": "on", "shop_slug": "",
                                                                    "shop_delivery_fee": "0"})
    assert response.status_code == 200
    assert Boutique.objects.get(pk=boutique.pk).shop_slug == SLUG   # inchangé


def test_publish_all_products(admin_client, boutique):
    _product(boutique, "A", online=False)
    _product(boutique, "B", online=False)
    admin_client.post(reverse("tenants:shop_settings"), {"action": "publish_all"})
    from apps.catalog.models import Product

    assert Product.objects.filter(compte=boutique.compte, is_online=True).count() == 2


def test_online_order_flagged_in_commande_list_and_detail(admin_client, boutique):
    _fill_cart(admin_client, boutique)
    admin_client.post(_url("checkout"), {**FORM, "payment_mode": "LIVRAISON"})
    commande = Invoice.objects.get()
    assert "bi-globe2" in admin_client.get(reverse("sales:commande_list")).content.decode()
    detail = admin_client.get(reverse("sales:invoice_detail", args=[commande.id])).content.decode()
    assert "boutique en ligne" in detail and "Akwa, Douala" in detail
    assert admin_client.get(reverse("sales:commande_search"), {"livraison": "EN_ATTENTE"}).json()["results"][0]["online"] is True


def test_product_form_has_online_checkbox(admin_client, boutique):
    Unit.objects.get_or_create(compte=boutique.compte, name="Pièce", defaults={"symbol": "pc"})
    html = admin_client.get(reverse("catalog:product_create")).content.decode()
    assert "is_online" in html


def test_publish_buttons_send_action_via_hidden_field(admin_client):
    """Le script anti double-clic désactive le bouton au submit : un
    name/value sur le bouton ne serait jamais envoyé (régression réelle)."""
    html = admin_client.get(reverse("tenants:shop_settings")).content.decode()
    assert 'type="hidden" name="action" value="publish_all"' in html
    assert '<button name="action"' not in html
