"""Numéro simple des commandes (1, 2, 3...) et colonne Action de la liste."""

import uuid
from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.sales import services
from apps.sales.models import Invoice
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


def _commande(boutique, type=Invoice.COMMANDE, **kwargs):
    return services.build_invoice(
        boutique=boutique, client=None, type=type, created_by=None,
        lines_data=[{"product": None, "description": "Article", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("1000"), "tva_rate": Decimal("0")}],
        **kwargs,
    )


def test_commandes_are_numbered_per_boutique(boutique):
    other = BoutiqueFactory()
    seqs = [_commande(boutique).commande_seq for _ in range(3)]
    assert seqs == [1, 2, 3]
    assert _commande(other).commande_seq == 1                 # autre boutique : son propre compteur


def test_cancelled_commande_number_is_not_reused(boutique):
    first = _commande(boutique)
    Invoice.objects.filter(pk=first.pk).update(status=Invoice.ANNULEE)
    assert _commande(boutique).commande_seq == 2


def test_devis_have_no_commande_number(boutique):
    assert _commande(boutique, type=Invoice.DEVIS).commande_seq is None
    assert _commande(boutique).commande_seq == 1               # les devis ne consomment pas de numéro


def test_replayed_commande_keeps_its_number(boutique):
    fixed = uuid.uuid4()
    first = _commande(boutique, id=fixed)
    again = _commande(boutique, id=fixed)                      # rejeu idempotent (synchro)
    assert again.pk == first.pk and again.commande_seq == 1
    assert _commande(boutique).commande_seq == 2


def test_list_shows_simple_number_and_eye(logged, boutique):
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=Invoice.EN_COURS)
    html = logged.get(reverse("sales:commande_list")).content.decode()
    assert commande.number not in html                          # plus de numéro de document long
    assert 'class="fw-semibold">1</td>' in html
    assert 'bi-eye' in html and reverse("sales:invoice_detail", args=[commande.id]) in html


def test_search_by_simple_number_and_json(logged, boutique):
    first, second = _commande(boutique), _commande(boutique)
    Invoice.objects.filter(pk__in=[first.pk, second.pk]).update(delivery_status=Invoice.EN_COURS)
    data = logged.get(reverse("sales:commande_search"), {"livraison": "EN_COURS", "q": "2"}).json()["results"]
    assert [row["number"] for row in data] == [2]
    assert data[0]["url"] == reverse("sales:invoice_detail", args=[second.id])
    everything = logged.get(reverse("sales:commande_search"), {"livraison": "EN_COURS"}).json()["results"]
    assert sorted(row["number"] for row in everything) == [1, 2]


def test_migration_numbering_helper_orders_by_creation(boutique):
    """Le rattrapage numérote les commandes existantes dans l'ordre de création."""
    import importlib

    from django.apps import apps as django_apps

    module = importlib.import_module("apps.sales.migrations.0011_invoice_commande_seq")
    a, b, c = _commande(boutique), _commande(boutique), _commande(boutique)
    Invoice.objects.filter(boutique=boutique).update(commande_seq=None)
    module.number_existing_commandes(django_apps, None)
    seqs = dict(Invoice.objects.filter(boutique=boutique).values_list("pk", "commande_seq"))
    assert [seqs[a.pk], seqs[b.pk], seqs[c.pk]] == [1, 2, 3]
