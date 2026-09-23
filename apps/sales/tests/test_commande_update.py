"""Modification d'une commande tant qu'elle n'est pas encore convertie en
facture — voir apps.sales.views.commande_update."""

from decimal import Decimal

import pytest
from django.test import override_settings
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


def _commande(boutique, client=None, **kwargs):
    return services.build_invoice(
        boutique=boutique, client=client, type=Invoice.COMMANDE, created_by=None,
        lines_data=[{"product": None, "description": "Article A", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("1000"), "tva_rate": Decimal("0")}],
        **kwargs,
    )


def _form_data(**overrides):
    data = {
        "client": "",
        "currency": "XOF",
        "discount_amount": "0",
        "note": "",
        "deposit_amount": "0",
        "form-TOTAL_FORMS": "1",
        "form-INITIAL_FORMS": "0",
        "form-MIN_NUM_FORMS": "0",
        "form-MAX_NUM_FORMS": "1000",
        "form-0-product": "",
        "form-0-description": "Article modifié",
        "form-0-quantity": "3",
        "form-0-unit_price_ht": "1500",
        "form-0-tva_rate": "0",
        "form-0-discount_amount": "",
    }
    data.update(overrides)
    return data


def test_get_edit_form_prefills_existing_lines(logged, boutique):
    awa = Client.objects.create(boutique=boutique, name="Awa")
    commande = _commande(boutique, client=awa)
    html = logged.get(reverse("sales:commande_update", args=[commande.id])).content.decode()
    assert "Article A" in html and "Modifier la commande" in html
    assert commande.number not in html   # remplacé par le n° simple ou le titre générique


def test_post_updates_lines_and_totals(logged, boutique):
    commande = _commande(boutique)
    response = logged.post(reverse("sales:commande_update", args=[commande.id]), _form_data())
    assert response.status_code == 302 and response.url == reverse("sales:invoice_detail", args=[commande.id])
    commande.refresh_from_db()
    line = commande.lines.get()
    assert line.description == "Article modifié" and line.quantity == 3 and line.unit_price_ht == 1500
    assert commande.total_ttc == 4500


def test_update_preserves_client_when_kept_and_can_change_it(logged, boutique):
    awa = Client.objects.create(boutique=boutique, name="Awa")
    moussa = Client.objects.create(boutique=boutique, name="Moussa")
    commande = _commande(boutique, client=awa)
    logged.post(reverse("sales:commande_update", args=[commande.id]), _form_data(client=str(moussa.id)))
    commande.refresh_from_db()
    assert commande.client_id == moussa.id


def test_update_never_touches_stock_or_status(logged, boutique):
    commande = _commande(boutique)
    status_before = commande.status
    logged.post(reverse("sales:commande_update", args=[commande.id]), _form_data())
    commande.refresh_from_db()
    assert commande.status == status_before == Invoice.BROUILLON


def test_no_deposit_is_ever_charged_through_update(logged, boutique):
    """Le formulaire d'édition porte encore le champ caché deposit_amount
    (partagé avec la création) : même rempli, il ne doit rien encaisser."""
    commande = _commande(boutique)
    logged.post(reverse("sales:commande_update", args=[commande.id]), _form_data(deposit_amount="500"))
    commande.refresh_from_db()
    assert not commande.payments.exists()


@pytest.mark.parametrize("status", [Invoice.CONVERTIE, Invoice.ANNULEE])
def test_converted_or_cancelled_commande_is_not_editable(logged, boutique, status):
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(status=status)
    response = logged.get(reverse("sales:commande_update", args=[commande.id]))
    assert response.status_code == 302 and response.url == reverse("sales:invoice_detail", args=[commande.id])
    response = logged.post(reverse("sales:commande_update", args=[commande.id]), _form_data())
    assert response.status_code == 302
    commande.refresh_from_db()
    assert commande.lines.get().description == "Article A"   # rien n'a bougé


def test_empty_lines_are_rejected(logged, boutique):
    commande = _commande(boutique)
    response = logged.post(reverse("sales:commande_update", args=[commande.id]), _form_data(**{
        "form-0-description": "", "form-TOTAL_FORMS": "1",
    }))
    assert response.status_code == 200   # réaffiche le formulaire, pas de redirection
    commande.refresh_from_db()
    assert commande.lines.get().description == "Article A"


def test_devis_and_facture_cannot_use_commande_update(logged, boutique):
    devis = services.build_invoice(
        boutique=boutique, client=None, type=Invoice.DEVIS, created_by=None,
        lines_data=[{"product": None, "description": "x", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("100"), "tva_rate": Decimal("0")}],
    )
    assert logged.get(reverse("sales:commande_update", args=[devis.id])).status_code == 404


def test_other_boutique_commande_is_404(logged):
    other = _commande(BoutiqueFactory())
    assert logged.get(reverse("sales:commande_update", args=[other.id])).status_code == 404


def test_cashier_can_edit_too(client, boutique):
    """Même périmètre que la création de commande (MANAGE_ROLES inclut le
    caissier) — un simple caissier peut donc modifier une commande."""
    cashier = User.objects.create_user(email="c@test.com", password="x")
    Membership.objects.create(user=cashier, boutique=boutique, role=Membership.CAISSIER, is_active=True)
    client.force_login(cashier)
    commande = _commande(boutique)
    assert client.get(reverse("sales:commande_update", args=[commande.id])).status_code == 200


@override_settings(IS_OFFLINE=True)
def test_no_edit_from_offline_workstation(client, boutique):
    from apps.sync.models import DeviceActivation

    user = User.objects.create_user(email="off@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    DeviceActivation.objects.create(
        boutique_id=boutique.id, boutique_name=boutique.name,
        compte_id=boutique.compte_id, compte_name=boutique.compte.name, token="x",
    )
    client.force_login(user)
    commande = _commande(boutique)
    response = client.get(reverse("sales:commande_update", args=[commande.id]))
    assert response.status_code == 302 and response.url == reverse("sales:invoice_detail", args=[commande.id])


def test_edit_button_shown_on_editable_commande_only(logged, boutique):
    commande = _commande(boutique)
    html = logged.get(reverse("sales:invoice_detail", args=[commande.id])).content.decode()
    assert reverse("sales:commande_update", args=[commande.id]) in html

    Invoice.objects.filter(pk=commande.pk).update(status=Invoice.CONVERTIE)
    html = logged.get(reverse("sales:invoice_detail", args=[commande.id])).content.decode()
    assert reverse("sales:commande_update", args=[commande.id]) not in html
