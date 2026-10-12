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
from apps.tenants.models import Boutique, DeliveryZone, Membership

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


FORM = {"name": "Awa", "phone": "677123456", "address": "Akwa, Douala", "payment_mode": "LIVRAISON",
        "confirm": "on"}


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


# --- présentation catalogue (promo, filtres, tri, achat direct) --------------------

def test_promotion_shows_badge_and_old_price(client, boutique):
    _product(boutique, "Promo", price="10000", compare_at_price=Decimal("15000"))
    html = client.get(_url("home")).content.decode()
    assert "Promotion" in html and "<span class=\"price-old\"" in html


def test_expired_promotion_is_not_shown(client, boutique):
    from datetime import timedelta

    from django.utils import timezone

    _product(boutique, "Fini", price="10000", compare_at_price=Decimal("15000"),
             promo_ends_at=timezone.now() - timedelta(hours=1))
    assert "<span class=\"price-old\"" not in client.get(_url("home")).content.decode()


def test_countdown_present_for_timed_promotion(client, boutique):
    from datetime import timedelta

    from django.utils import timezone

    _product(boutique, "Chrono", price="10000", compare_at_price=Decimal("15000"),
             promo_ends_at=timezone.now() + timedelta(hours=3))
    assert "data-countdown" in client.get(_url("home")).content.decode()


def test_no_promotion_when_compare_price_not_higher(client, boutique):
    _product(boutique, "Pas promo", price="10000", compare_at_price=Decimal("9000"))
    assert "<span class=\"price-old\"" not in client.get(_url("home")).content.decode()


def test_low_stock_gauge_shown(client, boutique):
    _product(boutique, "Peu", stock=3)
    _product(boutique, "Beaucoup", stock=50)
    html = client.get(_url("home")).content.decode()
    assert html.count("restants") == 1


def test_filter_available_only_and_price_range(client, boutique):
    _product(boutique, "Stock", price="1000", stock=5)
    _product(boutique, "Rupture", price="1000", stock=0)
    _product(boutique, "Cher", price="9000", stock=5)
    html = client.get(_url("home"), {"dispo": "1", "prix_max": "2000"}).content.decode()
    assert "Stock" in html and "Rupture" not in html and "Cher" not in html


def test_sorting_by_price(client, boutique):
    _product(boutique, "Aaa", price="3000")
    _product(boutique, "Bbb", price="1000")
    html = client.get(_url("home"), {"tri": "prix_asc"}).content.decode()
    assert html.index("Bbb") < html.index("Aaa")
    html = client.get(_url("home"), {"tri": "prix_desc"}).content.decode()
    assert html.index("Aaa") < html.index("Bbb")
    assert client.get(_url("home"), {"tri": "n_importe_quoi", "prix_min": "abc"}).status_code == 200


def test_card_has_buy_now_button_and_announcement(client, boutique):
    boutique.shop_delivery_info = "Livraison gratuite à Douala"
    boutique.save()
    _product(boutique)
    html = client.get(_url("home")).content.decode()
    assert "COMMANDER MAINTENANT" in html and "Livraison gratuite à Douala" in html and "articles" in html


def test_buy_now_replaces_cart_and_goes_to_checkout(client, boutique):
    first, second = _product(boutique, "Un"), _product(boutique, "Deux")
    client.post(_url("cart_add", first.id), {"quantity": 2})
    response = client.post(_url("cart_add", second.id), {"quantity": 1, "buy_now": "1"})
    assert response.status_code == 302 and response.url == _url("checkout")
    html = client.get(_url("cart")).content.decode()
    assert "Deux" in html and "Un<" not in html.replace("Deux", "")


def test_product_form_has_promo_fields(admin_client, boutique):
    Unit.objects.get_or_create(compte=boutique.compte, name="Pièce", defaults={"symbol": "pc"})
    html = admin_client.get(reverse("catalog:product_create")).content.decode()
    assert "compare_at_price" in html and "promo_ends_at" in html and 'type="datetime-local"' in html


# --- accès rapide dans la barre latérale -------------------------------------------

def test_sidebar_links_to_shop_when_enabled(admin_client, boutique):
    html = admin_client.get(reverse("core:home")).content.decode()
    assert "Voir ma boutique" in html and f"/boutique/{SLUG}/" in html
    assert reverse("tenants:shop_settings") in html and "Réglages boutique" in html


def test_sidebar_offers_activation_when_disabled(admin_client, boutique):
    boutique.shop_enabled = False
    boutique.save()
    html = admin_client.get(reverse("core:home")).content.decode()
    assert "Voir ma boutique" not in html and "Activer la boutique en ligne" in html


def test_sidebar_shop_settings_hidden_from_non_admin(client, boutique):
    user = User.objects.create_user(email="c@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.CAISSIER, is_active=True)
    client.force_login(user)
    html = client.get(reverse("core:home")).content.decode()
    assert "Voir ma boutique" in html and reverse("tenants:shop_settings") not in html


# --- zones de livraison (bandeau dynamique, frais par ville) -----------------------

def _zones(boutique):
    DeliveryZone.objects.create(boutique=boutique, city="Yaoundé", fee=0)
    DeliveryZone.objects.create(boutique=boutique, city="Douala", fee=0)
    DeliveryZone.objects.create(boutique=boutique, city="Bafoussam", fee=2000)
    DeliveryZone.objects.create(boutique=boutique, city="Garoua", fee=2000)


def test_banner_built_from_zones(boutique):
    _zones(boutique)
    banner = Boutique.objects.get(pk=boutique.pk).delivery_banner
    assert banner.startswith("Livraison gratuite à Douala et Yaoundé")
    assert "2 000 XAF à Bafoussam et Garoua" in banner


def test_banner_all_paid_zones_prefixed(boutique):
    DeliveryZone.objects.create(boutique=boutique, city="Kribi", fee=1500)
    assert Boutique.objects.get(pk=boutique.pk).delivery_banner == "Livraison : 1 500 XAF à Kribi"


def test_custom_text_overrides_generated_banner(boutique):
    _zones(boutique)
    boutique.shop_delivery_info = "Texte libre"
    boutique.save()
    assert boutique.delivery_banner == "Texte libre"


def test_banner_falls_back_to_single_fee_or_nothing(boutique):
    assert boutique.delivery_banner == ""
    boutique.shop_delivery_fee = Decimal("1000")
    assert "1 000 XAF" in boutique.delivery_banner


def test_banner_displayed_on_storefront(client, boutique):
    _zones(boutique)
    _product(boutique)
    html = client.get(_url("home")).content.decode()
    assert "Livraison gratuite à Douala et Yaoundé" in html


def test_checkout_requires_city_when_zones_exist(client, boutique):
    _zones(boutique)
    _fill_cart(client, boutique)
    assert client.post(_url("checkout"), FORM).status_code == 200
    assert not Invoice.objects.exists()


def test_checkout_applies_fee_of_chosen_city(client, boutique):
    _zones(boutique)
    _fill_cart(client, boutique, qty=2)          # 2 x 500
    zone = DeliveryZone.objects.get(boutique=boutique, city="Bafoussam")
    response = client.post(_url("checkout"), {**FORM, "zone": str(zone.id)})
    assert response.status_code == 302
    commande = Invoice.objects.get()
    assert commande.total_ttc == Decimal("3000")          # 1000 + 2000 de livraison
    assert commande.delivery_address.startswith("Bafoussam — ")
    assert any("Bafoussam" in line.description for line in commande.lines.all())


def test_free_city_adds_no_delivery_line(client, boutique):
    boutique.shop_delivery_fee = Decimal("9999")      # ignoré dès qu'il y a des zones
    boutique.save()
    _zones(boutique)
    _fill_cart(client, boutique, qty=1)
    zone = DeliveryZone.objects.get(boutique=boutique, city="Douala")
    client.post(_url("checkout"), {**FORM, "zone": str(zone.id)})
    commande = Invoice.objects.get()
    assert commande.total_ttc == Decimal("500") and commande.lines.count() == 1


def test_zone_of_another_boutique_rejected(client, boutique):
    _zones(boutique)
    other = BoutiqueFactory()
    foreign = DeliveryZone.objects.create(boutique=other, city="Ailleurs", fee=5)
    _fill_cart(client, boutique)
    assert client.post(_url("checkout"), {**FORM, "zone": str(foreign.id)}).status_code == 200
    assert not Invoice.objects.exists()


def test_cart_says_delivery_depends_on_city(client, boutique):
    _zones(boutique)
    _fill_cart(client, boutique)
    assert "calculée selon votre ville" in client.get(_url("cart")).content.decode()


def test_checkout_lists_cities_with_fees(client, boutique):
    _zones(boutique)
    _fill_cart(client, boutique)
    html = client.get(_url("checkout")).content.decode()
    assert "Bafoussam — 2000 XAF" in html and "Yaoundé — livraison gratuite" in html


def test_admin_manages_delivery_zones(admin_client, boutique):
    add = reverse("tenants:delivery_zone_add")
    assert admin_client.post(add, {"city": "  Limbé ", "fee": "1500"}).status_code == 302
    zone = DeliveryZone.objects.get(boutique=boutique)
    assert zone.city == "Limbé" and zone.fee == Decimal("1500")
    admin_client.post(add, {"city": "limbé", "fee": "0"})          # doublon (insensible à la casse)
    assert DeliveryZone.objects.filter(boutique=boutique).count() == 1
    html = admin_client.get(reverse("tenants:shop_settings")).content.decode()
    assert "Limbé" in html and "Bandeau affiché" in html
    admin_client.post(reverse("tenants:delivery_zone_delete", args=[zone.id]))
    assert not DeliveryZone.objects.filter(boutique=boutique).exists()


def test_cannot_delete_zone_of_other_boutique(admin_client, boutique):
    foreign = DeliveryZone.objects.create(boutique=BoutiqueFactory(), city="X", fee=0)
    assert admin_client.post(reverse("tenants:delivery_zone_delete", args=[foreign.id])).status_code == 404
    assert DeliveryZone.objects.filter(pk=foreign.pk).exists()


# --- page produit (description, avertissement, suggestions) ------------------------

def test_product_page_shows_description_notice_and_buy_now(client, boutique):
    boutique.shop_order_notice = "Commandez seulement si vous êtes décidé"
    boutique.save()
    p = _product(boutique, "Mastic", description="Répare les fissures. Sèche vite.")
    html = client.get(_url("product", p.id)).content.decode()
    assert "Répare les fissures." in html and "Commandez seulement si vous êtes décidé" in html
    assert 'name="buy_now"' in html and "stepper" in html


def test_product_page_hides_empty_description_and_notice(client, boutique):
    p = _product(boutique, "Simple")
    html = client.get(_url("product", p.id)).content.decode()
    assert 'class="pd-desc"' not in html and 'class="order-notice' not in html


def test_related_products_prefer_same_category_and_exclude_self(client, boutique):
    from apps.catalog.models import Category

    cat = Category.objects.create(compte=boutique.compte, name="Maison")
    main = _product(boutique, "Principal", category=cat)
    same = _product(boutique, "Meme cat", category=cat)
    for i in range(5):
        _product(boutique, f"Autre{i}")
    html = client.get(_url("product", main.id)).content.decode()
    related_html = html.split("Vous aimerez aussi")[1]
    assert "Meme cat" in related_html and "Principal" not in related_html
    assert related_html.count('class="p-card') == 4


def test_related_hidden_when_no_other_product(client, boutique):
    p = _product(boutique, "Seul")
    assert "Vous aimerez aussi" not in client.get(_url("product", p.id)).content.decode()


def test_related_excludes_unpublished(client, boutique):
    main = _product(boutique, "Principal")
    _product(boutique, "Cache", online=False)
    assert "Vous aimerez aussi" not in client.get(_url("product", main.id)).content.decode()


def test_shop_settings_saves_order_notice(admin_client, boutique):
    response = admin_client.post(reverse("tenants:shop_settings"), {
        "shop_enabled": "on", "shop_slug": SLUG, "shop_delivery_fee": "0",
        "shop_order_notice": "Soyez disponible", "shop_description": "", "shop_delivery_info": "",
    })
    assert response.status_code == 302
    assert Boutique.objects.get(pk=boutique.pk).shop_order_notice == "Soyez disponible"


def test_product_form_has_description(admin_client, boutique):
    Unit.objects.get_or_create(compte=boutique.compte, name="Pièce", defaults={"symbol": "pc"})
    assert "description" in admin_client.get(reverse("catalog:product_create")).content.decode()


# --- formulaire de commande en fenêtre (AJAX) ---------------------------------------

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def test_ajax_buy_now_returns_checkout_url(client, boutique):
    p = _product(boutique)
    response = client.post(_url("cart_add", p.id), {"quantity": 1, "buy_now": "1"}, **XHR)
    assert response.status_code == 200 and response.json() == {"checkout": _url("checkout"), "buy_now": True}


def test_checkout_modal_renders_only_the_form(client, boutique):
    _fill_cart(client, boutique)
    html = client.get(_url("checkout"), {"modal": "1"}).content.decode()
    assert 'id="checkout-root"' in html and "<html" not in html and "ACHETER MAINTENANT" in html
    full = client.get(_url("checkout")).content.decode()
    assert "<html" in full and 'id="checkout-root"' in full


def test_ajax_checkout_success_returns_redirect(client, boutique):
    _fill_cart(client, boutique)
    response = client.post(_url("checkout"), FORM, **XHR)
    commande = Invoice.objects.get()
    assert response.json() == {"redirect": _url("confirmation", commande.id)}


def test_ajax_checkout_errors_return_form_html(client, boutique):
    _fill_cart(client, boutique)
    response = client.post(_url("checkout"), {**FORM, "phone": "12"}, **XHR)
    assert response.status_code == 200 and "Numéro de téléphone invalide" in response.content.decode()
    assert not Invoice.objects.exists()


def test_delivery_confirmation_is_required(client, boutique):
    _fill_cart(client, boutique)
    data = {k: v for k, v in FORM.items() if k != "confirm"}
    response = client.post(_url("checkout"), data, **XHR)
    assert "vraiment être livré" in response.content.decode() and not Invoice.objects.exists()


def test_ajax_remove_item_then_empty_cart(client, boutique):
    p = _fill_cart(client, boutique)
    assert client.post(_url("cart_update", p.id), {"quantity": 0}, **XHR).json() == {"ok": True}
    assert client.get(_url("checkout"), **XHR).json() == {"empty": True}


def test_important_notice_and_footer_shown(client, boutique):
    boutique.shop_checkout_notice = "Soyez disponible pour la livraison"
    boutique.shop_order_footer = "Nous vous contacterons dans les 30 min"
    boutique.save()
    _fill_cart(client, boutique)
    html = client.get(_url("checkout"), {"modal": "1"}).content.decode()
    assert "IMPORTANT" in html and "Soyez disponible pour la livraison" in html and "30 min" in html


def test_no_important_block_without_notice(client, boutique):
    _fill_cart(client, boutique)
    assert "co-important" not in client.get(_url("checkout"), {"modal": "1"}).content.decode()


def test_modal_container_present_on_catalog(client, boutique):
    _product(boutique)
    assert 'id="co-modal"' in client.get(_url("home")).content.decode()


def test_shop_settings_saves_checkout_texts(admin_client, boutique):
    admin_client.post(reverse("tenants:shop_settings"), {
        "shop_enabled": "on", "shop_slug": SLUG, "shop_delivery_fee": "0",
        "shop_checkout_notice": "Important !", "shop_order_footer": "Merci !",
    })
    boutique.refresh_from_db()
    assert boutique.shop_checkout_notice == "Important !" and boutique.shop_order_footer == "Merci !"


# --- panier latéral -------------------------------------------------------------------

def test_cart_drawer_partial_lists_items_and_total(client, boutique):
    p = _fill_cart(client, boutique, qty=2, price="1500")
    html = client.get(_url("cart"), {"drawer": "1"}).content.decode()
    assert "<html" not in html and 'id="drawer-root"' in html
    assert p.name in html and "3000 XAF" in html and "Total estimé" in html and "data-co-open" in html
    assert 'data-count="2"' in html


def test_cart_drawer_via_ajax_header(client, boutique):
    _fill_cart(client, boutique)
    assert 'id="drawer-root"' in client.get(_url("cart"), **XHR).content.decode()


def test_cart_drawer_empty_state(client, boutique):
    html = client.get(_url("cart"), {"drawer": "1"}).content.decode()
    assert "Votre panier est vide" in html and "data-co-open" not in html


def test_cart_drawer_shows_old_price_for_promo(client, boutique):
    _fill_cart(client, boutique, price="10000", compare_at_price=Decimal("15000"))
    assert "price-old" in client.get(_url("cart"), {"drawer": "1"}).content.decode()


def test_cart_drawer_warns_on_stock_problem(client, boutique):
    p = _fill_cart(client, boutique, qty=5, stock=5)
    stock_services.apply_movement(
        boutique=boutique, product=p, type=StockMovement.AJUSTEMENT, quantity=Decimal("-3"), created_by=None,
    )
    html = client.get(_url("cart"), {"drawer": "1"}).content.decode()
    assert "Stock insuffisant" in html and "Corrigez les quantités" in html and "data-co-open" not in html


def test_cart_drawer_delivery_note_by_city(client, boutique):
    _zones(boutique)
    _fill_cart(client, boutique)
    assert "calculée selon votre ville" in client.get(_url("cart"), {"drawer": "1"}).content.decode()


def test_ajax_add_to_cart_updates_count(client, boutique):
    p = _product(boutique)
    response = client.post(_url("cart_add", p.id), {"quantity": 3}, **XHR)
    assert response.json()["buy_now"] is False
    assert 'data-count="3"' in client.get(_url("cart"), {"drawer": "1"}).content.decode()


def test_drawer_markup_and_cart_badge_on_pages(client, boutique):
    _product(boutique)
    html = client.get(_url("home")).content.decode()
    assert 'id="cart-drawer"' in html and 'id="cart-link"' in html and 'id="cart-badge"' in html


# --- en-tête : logo seul ---------------------------------------------------------------

def test_header_shows_logo_without_name_when_logo_exists(client, boutique):
    boutique.compte.logo = "logos/test.webp"
    boutique.compte.save()
    html = client.get(_url("home")).content.decode()
    header = html.split("<header")[1].split("</header>")[0]
    assert 'alt="' in header and "<span>" + boutique.name + "</span>" not in header


def test_header_falls_back_to_name_without_logo(client, boutique):
    header = client.get(_url("home")).content.decode().split("<header")[1].split("</header>")[0]
    assert "<span>" + boutique.name + "</span>" in header


def test_description_has_about_heading_and_paragraphs(client, boutique):
    p = _product(boutique, "Mastic", description="Premier paragraphe." + chr(10) * 2 + "Second paragraphe.")
    html = client.get(_url("product", p.id)).content.decode()
    assert "À propos du produit" in html and "<p>Premier paragraphe.</p>" in html and "<p>Second paragraphe.</p>" in html
    assert "inter.css" in html


def test_about_section_hidden_without_description(client, boutique):
    p = _product(boutique, "Simple")
    assert "À propos du produit" not in client.get(_url("product", p.id)).content.decode()
