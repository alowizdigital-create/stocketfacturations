"""Statuts de livraison d'une commande : EN_ATTENTE <-> EN_COURS <-> EN_MAGASIN,
puis LIVREE (qui valide aussi la commande) — voir apps.sales.services et
apps.sales.views.commande_advance."""

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
    user = User.objects.create_user(email="g@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    client.force_login(user)
    return client


def _commande(boutique, delivery_status=Invoice.EN_ATTENTE, **kwargs):
    commande = services.build_invoice(
        boutique=boutique, client=None, type=Invoice.COMMANDE, created_by=None,
        lines_data=[{"product": None, "description": "Article", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("1000"), "tva_rate": Decimal("0")}],
        **kwargs,
    )
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=delivery_status)
    commande.refresh_from_db()
    return commande


def _advance(client, commande, seen, to, next_url=None):
    return client.post(reverse("sales:commande_advance", args=[commande.id]), {
        "from": seen, "to": to, "next": next_url or "",
    })


# --- services ---------------------------------------------------------------

def test_mark_en_attente_requires_en_cours(boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_ATTENTE)
    with pytest.raises(ValueError):
        services.mark_commande_en_attente(commande)
    commande.delivery_status = Invoice.EN_COURS
    services.mark_commande_en_attente(commande)
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.EN_ATTENTE


def test_mark_en_magasin_requires_en_cours(boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_MAGASIN)
    with pytest.raises(ValueError):
        services.mark_commande_en_magasin(commande)   # déjà en magasin
    commande.delivery_status = Invoice.EN_COURS
    services.mark_commande_en_magasin(commande)
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.EN_MAGASIN


def test_mark_en_cours_reverts_from_en_magasin(boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_MAGASIN)
    services.mark_commande_en_cours(commande)
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.EN_COURS


def test_transitions_reject_non_commande_documents(boutique):
    devis = services.build_invoice(
        boutique=boutique, client=None, type=Invoice.DEVIS, created_by=None,
        lines_data=[{"product": None, "description": "x", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("100"), "tva_rate": Decimal("0")}],
    )
    with pytest.raises(ValueError):
        services.mark_commande_en_attente(devis)
    with pytest.raises(ValueError):
        services.mark_commande_en_magasin(devis)


# --- vue commande_advance ----------------------------------------------------

def test_en_cours_can_go_back_to_en_attente(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_COURS)
    _advance(logged, commande, Invoice.EN_COURS, Invoice.EN_ATTENTE)
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.EN_ATTENTE


def test_en_cours_can_go_to_en_magasin(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_COURS)
    _advance(logged, commande, Invoice.EN_COURS, Invoice.EN_MAGASIN)
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.EN_MAGASIN


def test_en_magasin_can_go_back_to_en_cours(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_MAGASIN)
    _advance(logged, commande, Invoice.EN_MAGASIN, Invoice.EN_COURS)
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.EN_COURS


def test_en_cours_can_be_delivered_directly(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_COURS)
    _advance(logged, commande, Invoice.EN_COURS, Invoice.LIVREE)
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.LIVREE and commande.status == Invoice.CONVERTIE


def test_en_magasin_can_be_delivered(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_MAGASIN)
    _advance(logged, commande, Invoice.EN_MAGASIN, Invoice.LIVREE)
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.LIVREE


def test_stale_state_is_rejected(logged, boutique):
    """Le formulaire envoie l'état vu par l'utilisateur (`from`) — s'il ne
    correspond plus à l'état réel, rien n'est modifié."""
    commande = _commande(boutique, delivery_status=Invoice.EN_MAGASIN)
    _advance(logged, commande, Invoice.EN_COURS, Invoice.EN_MAGASIN)   # croit encore être EN_COURS
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.EN_MAGASIN   # inchangé


@pytest.mark.parametrize("seen,to", [
    (Invoice.EN_ATTENTE, Invoice.LIVREE),    # ne peut pas sauter direct de en attente à livrée
    (Invoice.EN_ATTENTE, Invoice.EN_MAGASIN),  # ne peut pas sauter direct en magasin
    (Invoice.LIVREE, Invoice.EN_COURS),      # pas via ce formulaire (réservé à l'admin, voir la fiche)
])
def test_disallowed_transitions_are_rejected(logged, boutique, seen, to):
    commande = _commande(boutique, delivery_status=seen)
    _advance(logged, commande, seen, to)
    commande.refresh_from_db()
    assert commande.delivery_status == seen   # inchangé


def test_cancelled_commande_cannot_advance(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_COURS)
    Invoice.objects.filter(pk=commande.pk).update(status=Invoice.ANNULEE)
    _advance(logged, commande, Invoice.EN_COURS, Invoice.EN_MAGASIN)
    commande.refresh_from_db()
    assert commande.delivery_status == Invoice.EN_COURS


def test_advance_redirects_to_next_when_safe(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_COURS)
    response = _advance(logged, commande, Invoice.EN_COURS, Invoice.EN_ATTENTE, next_url="/commandes/?livraison=EN_COURS")
    assert response.status_code == 302 and response.url == "/commandes/?livraison=EN_COURS"


def test_advance_ignores_unsafe_next(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_COURS)
    response = _advance(logged, commande, Invoice.EN_COURS, Invoice.EN_ATTENTE, next_url="https://evil.test/")
    assert response.status_code == 302 and response.url == reverse("sales:commande_list")


# --- liste : onglets et actions ---------------------------------------------

def test_en_magasin_tab_lists_its_commandes(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_MAGASIN)
    html = logged.get(reverse("sales:commande_list"), {"livraison": "EN_MAGASIN"}).content.decode()
    assert str(commande.commande_seq) in html and "En magasin" in html


def test_en_cours_row_has_three_actions(logged, boutique):
    _commande(boutique, delivery_status=Invoice.EN_COURS)
    html = logged.get(reverse("sales:commande_list"), {"livraison": "EN_COURS"}).content.decode()
    assert "Remettre en attente" in html
    assert "Envoyer en magasin" in html
    assert "Marquer comme livrée" in html or "value=\"LIVREE\"" in html


def test_en_magasin_row_has_two_actions(logged, boutique):
    _commande(boutique, delivery_status=Invoice.EN_MAGASIN)
    html = logged.get(reverse("sales:commande_list"), {"livraison": "EN_MAGASIN"}).content.decode()
    assert "Remettre en cours de préparation" in html


def test_search_json_includes_actions_list(logged, boutique):
    _commande(boutique, delivery_status=Invoice.EN_COURS)
    data = logged.get(reverse("sales:commande_search"), {"livraison": "EN_COURS"}).json()["results"]
    actions = data[0]["actions"]
    assert len(actions) == 3
    targets = {a["to"] for a in actions}
    assert targets == {Invoice.EN_ATTENTE, Invoice.EN_MAGASIN, Invoice.LIVREE}


def test_livree_commande_has_no_actions_in_list(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_COURS)
    services.deliver_commande(commande)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=Invoice.LIVREE)
    html = logged.get(reverse("sales:commande_list"), {"livraison": "LIVREE"}).content.decode()
    assert "Terminée" in html


# --- fiche commande -----------------------------------------------------------

def test_detail_page_shows_store_and_wait_buttons_for_en_cours(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_COURS)
    html = logged.get(reverse("sales:invoice_detail", args=[commande.id])).content.decode()
    assert "Remettre en attente" in html and "Envoyer en magasin" in html and "Marquer comme livrée" in html


def test_detail_page_shows_revert_and_deliver_for_en_magasin(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_MAGASIN)
    html = logged.get(reverse("sales:invoice_detail", args=[commande.id])).content.decode()
    assert "Remettre en préparation" in html and "Marquer comme livrée" in html


def test_detail_page_buttons_use_commande_advance(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_COURS)
    html = logged.get(reverse("sales:invoice_detail", args=[commande.id])).content.decode()
    assert reverse("sales:commande_advance", args=[commande.id]) in html
