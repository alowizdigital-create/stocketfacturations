"""Liste des commandes sur téléphone : une commande = une mini-fiche, boutons sur
une seule ligne — voir apps/sales/templates/sales/commande_list.html et le bloc
« Liste des commandes sur téléphone » de static/css/app.css."""

from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.urls import reverse

from apps.accounts.models import User
from apps.sales import services
from apps.sales.models import Client, Invoice
from apps.sync.tests.factories import BoutiqueFactory
from apps.tenants.models import Membership

pytestmark = pytest.mark.django_db


@pytest.fixture
def boutique():
    return BoutiqueFactory()


@pytest.fixture
def logged(client, boutique):
    user = User.objects.create_user(email="m@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    client.force_login(user)
    return client


def _commande(boutique):
    customer = Client.objects.create(boutique=boutique, name="Awa", phone="677123456")
    return services.build_invoice(
        boutique=boutique, client=customer, type=Invoice.COMMANDE, created_by=None,
        lines_data=[{"product": None, "description": "Article", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("1000"), "tva_rate": Decimal("0")}],
    )


def test_rows_carry_column_classes_for_the_mobile_card_layout(logged, boutique):
    _commande(boutique)
    html = logged.get(reverse("sales:commande_list"), {"livraison": "EN_ATTENTE"}).content.decode()
    assert "cmd-table" in html
    for cls in ("c-num", "c-client", "c-phone", "c-date", "c-total", "c-actions"):
        assert f'class="' in html and cls in html


def test_live_search_markup_uses_the_same_classes(logged):
    html = logged.get(reverse("sales:commande_list")).content.decode()
    # le gabarit JS qui redessine les lignes après une recherche
    js = html.split("function renderRows")[1]
    for cls in ("c-num", "c-client", "c-phone", "c-date", "c-total", "c-actions"):
        assert cls in js


def test_mobile_css_puts_actions_on_one_line():
    css = (Path(settings.BASE_DIR) / "static" / "css" / "app.css").read_text(encoding="utf-8")
    block = css.split("Liste des commandes sur téléphone")[1]
    assert "max-width: 767.98px" in block and "grid-template-areas" in block
    assert "flex-wrap: nowrap" in block and ".cmd-table thead { display: none; }" in block


def test_stylesheet_version_bumped_so_phones_reload_it(logged):
    html = logged.get(reverse("core:home")).content.decode()
    assert "css/app.css?v=mobile6" in html


def test_actions_still_present_in_each_row(logged, boutique):
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=Invoice.EN_COURS)
    html = logged.get(reverse("sales:commande_list"), {"livraison": "EN_COURS"}).content.decode()
    assert reverse("sales:invoice_detail", args=[commande.id]) in html
    assert html.count(reverse("sales:commande_advance", args=[commande.id])) == 3      # attente, magasin, livrée
