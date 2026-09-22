"""Liste des commandes : colonne téléphone du client (à la place du statut)
et bouton pour copier ce numéro."""

from decimal import Decimal

import pytest
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
    user = User.objects.create_user(email="c@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    client.force_login(user)
    return client


def _commande(boutique, client=None, **kwargs):
    return services.build_invoice(
        boutique=boutique, client=client, type=Invoice.COMMANDE, created_by=None,
        lines_data=[{"product": None, "description": "Article", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("1000"), "tva_rate": Decimal("0")}],
        **kwargs,
    )


def test_list_shows_client_phone_instead_of_status(logged, boutique):
    awa = Client.objects.create(boutique=boutique, name="Awa Traoré", phone="0701020304")
    commande = _commande(boutique, client=awa)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=Invoice.EN_COURS)
    html = logged.get(reverse("sales:commande_list")).content.decode()
    assert "0701020304" in html
    assert "Statut" not in html
    assert "Téléphone" in html


def test_list_shows_dash_without_client_phone(logged, boutique):
    no_client = _commande(boutique)
    sans_tel = Client.objects.create(boutique=boutique, name="Sans tél")
    commande2 = _commande(boutique, client=sans_tel)
    Invoice.objects.filter(pk__in=[no_client.pk, commande2.pk]).update(delivery_status=Invoice.EN_COURS)
    html = logged.get(reverse("sales:commande_list")).content.decode()
    assert "copy-phone-btn" in html and "disabled" in html


def test_copy_button_present_and_carries_the_phone(logged, boutique):
    awa = Client.objects.create(boutique=boutique, name="Awa", phone="0701020304")
    commande = _commande(boutique, client=awa)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=Invoice.EN_COURS)
    html = logged.get(reverse("sales:commande_list")).content.decode()
    assert 'data-phone="0701020304"' in html
    assert "copy-phone-btn" in html


def test_search_json_includes_client_phone(logged, boutique):
    awa = Client.objects.create(boutique=boutique, name="Awa", phone="0701020304")
    commande = _commande(boutique, client=awa)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=Invoice.EN_COURS)
    data = logged.get(reverse("sales:commande_search"), {"livraison": "EN_COURS"}).json()["results"]
    assert data[0]["client_phone"] == "0701020304"


def test_search_json_phone_empty_without_client(logged, boutique):
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=Invoice.EN_COURS)
    data = logged.get(reverse("sales:commande_search"), {"livraison": "EN_COURS"}).json()["results"]
    assert data[0]["client_phone"] == ""
