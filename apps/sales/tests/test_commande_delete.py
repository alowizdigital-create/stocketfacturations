"""Suppression définitive d'une commande jamais entamée — voir
apps.sales.services.delete_commande et apps.sales.views.commande_delete."""

from decimal import Decimal

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.sales import services
from apps.sales.models import Client, Invoice, Payment
from apps.sync.tests.factories import BoutiqueFactory
from apps.tenants.models import Membership

pytestmark = pytest.mark.django_db


@pytest.fixture
def boutique():
    return BoutiqueFactory()


@pytest.fixture
def logged(client, boutique):
    user = User.objects.create_user(email="g@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    client.force_login(user)
    return client


def _commande(boutique, **kwargs):
    return services.build_invoice(
        boutique=boutique, client=None, type=Invoice.COMMANDE, created_by=None,
        lines_data=[{"product": None, "description": "Article", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("1000"), "tva_rate": Decimal("0")}],
        **kwargs,
    )


# --- service ------------------------------------------------------------------

def test_delete_removes_the_row(boutique):
    commande = _commande(boutique)
    pk = commande.pk
    services.delete_commande(commande)
    assert not Invoice.objects.filter(pk=pk).exists()


def test_delete_cascades_deposit_payment(boutique):
    """Choix assumé (suppression définitive, pas une annulation) : un
    acompte versé à la création disparaît avec la commande."""
    commande = _commande(boutique)
    Payment.objects.create(invoice=commande, boutique=boutique, amount=Decimal("300"))
    services.delete_commande(commande)
    assert not Payment.objects.filter(invoice_id=commande.pk).exists()


@pytest.mark.parametrize("delivery_status", [Invoice.EN_COURS, Invoice.EN_MAGASIN, Invoice.LIVREE])
def test_delete_refused_once_delivery_started(boutique, delivery_status):
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=delivery_status)
    commande.refresh_from_db()
    with pytest.raises(ValueError):
        services.delete_commande(commande)
    assert Invoice.objects.filter(pk=commande.pk).exists()


def test_delete_refused_once_validated_even_if_still_en_attente(boutique):
    """Une commande peut être validée (facture générée) sans jamais être
    passée par le circuit de livraison — toujours dangereux à supprimer."""
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(status=Invoice.CONVERTIE)
    commande.refresh_from_db()
    with pytest.raises(ValueError):
        services.delete_commande(commande)


def test_delete_refused_for_annulee(boutique):
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(status=Invoice.ANNULEE)
    commande.refresh_from_db()
    with pytest.raises(ValueError):
        services.delete_commande(commande)


def test_delete_rejects_non_commande():
    from apps.sync.tests.factories import BoutiqueFactory as BF

    devis = services.build_invoice(
        boutique=BF(), client=None, type=Invoice.DEVIS, created_by=None,
        lines_data=[{"product": None, "description": "x", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("100"), "tva_rate": Decimal("0")}],
    )
    with pytest.raises(ValueError):
        services.delete_commande(devis)


# --- vue ------------------------------------------------------------------------

def test_view_deletes_and_redirects(logged, boutique):
    commande = _commande(boutique)
    response = logged.post(reverse("sales:commande_delete", args=[commande.id]))
    assert response.status_code == 302 and response.url == reverse("sales:commande_list")
    assert not Invoice.objects.filter(pk=commande.pk).exists()


def test_view_redirects_to_next_when_provided(logged, boutique):
    commande = _commande(boutique)
    response = logged.post(reverse("sales:commande_delete", args=[commande.id]), {"next": "/commandes/?livraison=EN_ATTENTE"})
    assert response.status_code == 302 and response.url == "/commandes/?livraison=EN_ATTENTE"


def test_view_ignores_unsafe_next(logged, boutique):
    commande = _commande(boutique)
    response = logged.post(reverse("sales:commande_delete", args=[commande.id]), {"next": "https://evil.test/"})
    assert response.url == reverse("sales:commande_list")


def test_view_refuses_started_commande_and_keeps_it(logged, boutique):
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=Invoice.EN_COURS)
    response = logged.post(reverse("sales:commande_delete", args=[commande.id]))
    assert response.status_code == 302 and response.url == reverse("sales:invoice_detail", args=[commande.id])
    assert Invoice.objects.filter(pk=commande.pk).exists()


def test_view_requires_post(logged, boutique):
    commande = _commande(boutique)
    assert logged.get(reverse("sales:commande_delete", args=[commande.id])).status_code == 405
    assert Invoice.objects.filter(pk=commande.pk).exists()


def test_view_scoped_to_boutique(logged, boutique):
    other = _commande(BoutiqueFactory())
    assert logged.post(reverse("sales:commande_delete", args=[other.id])).status_code == 404
    assert Invoice.objects.exists()


@override_settings(IS_OFFLINE=True)
def test_view_refused_offline(boutique):
    from django.test import Client

    from apps.sync.models import DeviceActivation

    user = User.objects.create_user(email="off@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    DeviceActivation.objects.create(
        boutique_id=boutique.id, boutique_name=boutique.name,
        compte_id=boutique.compte_id, compte_name=boutique.compte.name, token="x",
    )
    client = Client()
    client.force_login(user)
    commande = _commande(boutique)
    response = client.post(reverse("sales:commande_delete", args=[commande.id]))
    assert response.status_code == 302
    assert Invoice.objects.filter(pk=commande.pk).exists()


def test_cashier_can_delete_too(client, boutique):
    """Même périmètre que les autres actions de la liste des commandes
    (MANAGE_ROLES inclut le caissier)."""
    cashier = User.objects.create_user(email="c@test.com", password="x")
    Membership.objects.create(user=cashier, boutique=boutique, role=Membership.CAISSIER, is_active=True)
    client.force_login(cashier)
    commande = _commande(boutique)
    response = client.post(reverse("sales:commande_delete", args=[commande.id]))
    assert response.status_code == 302
    assert not Invoice.objects.filter(pk=commande.pk).exists()


# --- affichage ------------------------------------------------------------------

def test_delete_button_shown_only_for_pending_unvalidated(logged, boutique):
    deletable = _commande(boutique)
    started = _commande(boutique)
    Invoice.objects.filter(pk=started.pk).update(delivery_status=Invoice.EN_COURS)

    html = logged.get(reverse("sales:commande_list"), {"livraison": "EN_ATTENTE"}).content.decode()
    assert reverse("sales:commande_delete", args=[deletable.id]) in html

    html = logged.get(reverse("sales:commande_list"), {"livraison": "EN_COURS"}).content.decode()
    assert reverse("sales:commande_delete", args=[started.id]) not in html


def test_delete_button_hidden_once_validated_though_still_en_attente(logged, boutique):
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(status=Invoice.CONVERTIE)
    html = logged.get(reverse("sales:commande_list"), {"livraison": "EN_ATTENTE"}).content.decode()
    assert reverse("sales:commande_delete", args=[commande.id]) not in html


def test_search_json_includes_delete_url(logged, boutique):
    commande = _commande(boutique)
    data = logged.get(reverse("sales:commande_search"), {"livraison": "EN_ATTENTE"}).json()["results"]
    assert data[0]["delete_url"] == reverse("sales:commande_delete", args=[commande.id])


def test_delete_button_on_detail_page(logged, boutique):
    commande = _commande(boutique)
    html = logged.get(reverse("sales:invoice_detail", args=[commande.id])).content.decode()
    assert reverse("sales:commande_delete", args=[commande.id]) in html
