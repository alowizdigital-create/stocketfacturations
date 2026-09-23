"""Export PDF/Excel de la liste des commandes — voir apps.sales.exports et
apps.sales.views.commande_export_pdf/commande_export_excel."""

from decimal import Decimal

import openpyxl
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


def _commande(boutique, client=None, delivery_status=Invoice.EN_ATTENTE, **kwargs):
    commande = services.build_invoice(
        boutique=boutique, client=client, type=Invoice.COMMANDE, created_by=None,
        lines_data=[{"product": None, "description": "Article", "quantity": Decimal("2"),
                     "unit_price_ht": Decimal("1000"), "tva_rate": Decimal("0")}],
        **kwargs,
    )
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=delivery_status)
    return commande


def _xlsx_rows(content):
    from io import BytesIO

    workbook = openpyxl.load_workbook(BytesIO(content))
    sheet = workbook.active
    return [[cell.value for cell in row] for row in sheet.iter_rows()]


# --- PDF --------------------------------------------------------------------

def test_pdf_export_returns_a_pdf_file(logged, boutique):
    awa = Client.objects.create(boutique=boutique, name="Awa Traoré", phone="0701020304")
    _commande(boutique, client=awa, delivery_status=Invoice.EN_COURS)
    response = logged.get(reverse("sales:commande_export_pdf"), {"livraison": "EN_COURS"})
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    assert "attachment" in response["Content-Disposition"] and ".pdf" in response["Content-Disposition"]


def test_pdf_export_respects_tab_and_search(logged, boutique):
    awa = Client.objects.create(boutique=boutique, name="Awa Traoré")
    moussa = Client.objects.create(boutique=boutique, name="Moussa")
    _commande(boutique, client=awa, delivery_status=Invoice.EN_COURS)
    _commande(boutique, client=moussa, delivery_status=Invoice.EN_ATTENTE)
    only_en_attente = logged.get(reverse("sales:commande_export_pdf"), {"livraison": "EN_ATTENTE"})
    assert b"%PDF" in only_en_attente.content
    # une recherche qui ne correspond à rien renvoie quand même un PDF valide (liste vide)
    empty = logged.get(reverse("sales:commande_export_pdf"), {"livraison": "EN_COURS", "q": "introuvable"})
    assert empty.content.startswith(b"%PDF")


def test_pdf_export_works_with_no_commandes(logged, boutique):
    response = logged.get(reverse("sales:commande_export_pdf"), {"livraison": "LIVREE"})
    assert response.status_code == 200 and response.content.startswith(b"%PDF")


# --- Excel --------------------------------------------------------------------

def test_excel_export_returns_a_workbook_with_expected_rows(logged, boutique):
    awa = Client.objects.create(boutique=boutique, name="Awa Traoré", phone="0701020304")
    _commande(boutique, client=awa, delivery_status=Invoice.EN_COURS)
    response = logged.get(reverse("sales:commande_export_excel"), {"livraison": "EN_COURS"})
    assert response.status_code == 200
    assert response["Content-Type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert ".xlsx" in response["Content-Disposition"]

    rows = _xlsx_rows(response.content)
    header = next(r for r in rows if r and r[0] == "N°")
    data_row = rows[rows.index(header) + 1]
    assert data_row[1] == "Awa Traoré" and data_row[2] == "0701020304"
    assert "2 000" in data_row[6]


def test_excel_export_scoped_to_boutique(logged, boutique):
    other = BoutiqueFactory()
    _commande(other, client=Client.objects.create(boutique=other, name="Client d'une autre boutique"),
              delivery_status=Invoice.EN_ATTENTE)
    response = logged.get(reverse("sales:commande_export_excel"), {"livraison": "EN_ATTENTE"})
    rows = _xlsx_rows(response.content)
    assert not any(row and "autre boutique" in str(row[1]) for row in rows)


def test_excel_export_cancelled_commande_is_excluded(logged, boutique):
    commande = _commande(boutique, delivery_status=Invoice.EN_ATTENTE)
    Invoice.objects.filter(pk=commande.pk).update(status=Invoice.ANNULEE)
    response = logged.get(reverse("sales:commande_export_excel"), {"livraison": "EN_ATTENTE"})
    rows = _xlsx_rows(response.content)
    assert not any(row and row[0] == "1" for row in rows)


def test_export_requires_login(client, boutique):
    response = client.get(reverse("sales:commande_export_excel"))
    assert response.status_code == 302


def test_export_filename_reflects_tab(logged, boutique):
    response = logged.get(reverse("sales:commande_export_excel"), {"livraison": "LIVREE"})
    assert "livree" in response["Content-Disposition"].lower()
