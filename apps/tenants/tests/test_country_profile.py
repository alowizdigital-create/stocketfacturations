"""Profil de départ Cameroun (XAF, TVA 19,25 %) et mentions NIU/RCCM — voir
apps.core.country_profiles, apps.tenants.services.create_company_with_owner
et Boutique.legal_mentions."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.sales import services
from apps.sales.models import Invoice
from apps.sales.pdf import render_invoice_pdf
from apps.sync.tests.factories import BoutiqueFactory
from apps.tenants.models import Boutique, Membership
from apps.tenants.services import create_company_with_owner

pytestmark = pytest.mark.django_db


def _create(country="", devise="XOF"):
    return create_company_with_owner(
        user_model=User, entreprise_name="Ma Boite", boutique_name="B1", devise=devise,
        email="cm@test.com", password="Xk9!longpass", country=country,
    )


# --- profil pays ----------------------------------------------------------------

def test_cameroon_profile_sets_xaf_tva_and_calling_code():
    _, boutique = _create("CM", devise="XOF")
    assert boutique.devise == "XAF"
    assert boutique.tva_rate == Decimal("19.25")
    assert boutique.country_calling_code == "237"


def test_no_profile_keeps_chosen_devise_and_zero_tva():
    _, boutique = _create("", devise="XOF")
    assert (boutique.devise, boutique.tva_rate, boutique.country_calling_code) == ("XOF", Decimal("0"), "")


def test_signup_form_defaults_to_cameroon(client):
    html = client.get(reverse("tenants:signup")).content.decode()
    assert 'value="CM" selected' in html


def test_signup_with_cameroon_overrides_devise(client):
    response = client.post(reverse("tenants:signup"), {
        "entreprise_name": "Douala SARL", "boutique_name": "Akwa", "country": "CM", "devise": "XOF",
        "email": "d@test.com", "password": "Xk9!longpass", "password_confirm": "Xk9!longpass",
    })
    assert response.status_code == 302
    boutique = Boutique.objects.get(compte__name="Douala SARL")
    assert boutique.devise == "XAF" and boutique.tva_rate == Decimal("19.25")


# --- NIU / RCCM -----------------------------------------------------------------

@pytest.fixture
def boutique():
    b = BoutiqueFactory()
    b.compte.niu, b.compte.rccm = "M012345678901A", "RC/DLA/2024/B/1234"
    b.compte.save()
    return b


def _invoice(boutique, pdf_format=Invoice.PDF_FORMAT_80MM):
    return services.build_invoice(
        boutique=boutique, client=None, type=Invoice.FACTURE, created_by=None, pdf_format=pdf_format,
        lines_data=[{"product": None, "description": "Article", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("1000"), "tva_rate": Decimal("19.25")}],
    )


def test_legal_mentions_listed_only_when_filled():
    assert BoutiqueFactory().legal_mentions == []
    b = BoutiqueFactory()
    b.compte.niu = "M1"
    assert b.legal_mentions == ["NIU : M1"]


@pytest.mark.parametrize("fmt", [Invoice.PDF_FORMAT_80MM, Invoice.PDF_FORMAT_A4])
def test_pdf_contains_niu_and_rccm(boutique, fmt, monkeypatch):
    # Flux de page non compressé pour pouvoir y chercher le texte (pas de
    # lecteur PDF dans les dépendances).
    monkeypatch.setattr("reportlab.rl_config.pageCompression", 0)
    pdf = render_invoice_pdf(_invoice(boutique, fmt))
    assert b"M012345678901A" in pdf and b"RC/DLA/2024/B/1234" in pdf


def test_public_page_shows_mentions(client, boutique):
    invoice = _invoice(boutique)
    html = client.get(reverse("sales:invoice_public_view", args=[invoice.id])).content.decode()
    assert "NIU : M012345678901A" in html


def test_company_settings_saves_niu_rccm(client, boutique):
    user = User.objects.create_user(email="a@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.ADMIN_COMPTE, is_active=True)
    client.force_login(user)
    response = client.post(reverse("tenants:company_settings"), {
        "name": boutique.compte.name, "email": "", "phone": "", "niu": "NEW1", "rccm": "RC2",
    })
    assert response.status_code == 302
    boutique.compte.refresh_from_db()
    assert (boutique.compte.niu, boutique.compte.rccm) == ("NEW1", "RC2")


# --- TVA par défaut ---------------------------------------------------------------

def test_new_invoice_line_defaults_to_boutique_tva(client, boutique):
    boutique.tva_rate = Decimal("19.25")
    boutique.save()
    user = User.objects.create_user(email="g@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    client.force_login(user)
    html = client.get(reverse("sales:invoice_create")).content.decode()
    assert 'value="19.25"' in html
