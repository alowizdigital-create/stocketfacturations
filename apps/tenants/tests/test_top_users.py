"""Classement des utilisateurs les plus actifs — réservé au super-admin de
la plateforme (voir apps.tenants.platform_views.platform_top_users)."""

from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.sales import services as sales_services
from apps.sales.models import Invoice, Payment
from apps.stock import services as stock_services
from apps.stock.models import StockMovement
from apps.sync.tests.factories import BoutiqueFactory, ProductFactory
from apps.tenants.models import Membership
from apps.tenants.platform_views import _top_active_users

pytestmark = pytest.mark.django_db


@pytest.fixture
def boutique():
    return BoutiqueFactory()


def _staff(boutique, email, role=Membership.CAISSIER):
    user = User.objects.create_user(email=email, password="x")
    Membership.objects.create(user=user, boutique=boutique, role=role, is_active=True)
    return user


def _sale(boutique, user, n=1):
    for _ in range(n):
        sale = sales_services.build_sale(
            boutique=boutique, client=None, created_by=user,
            lines_data=[{"product": None, "description": "x", "quantity": Decimal("1"),
                         "unit_price_ht": Decimal("100"), "tva_rate": Decimal("0")}],
        )
        sales_services.confirm_sale(sale, created_by=user)


def test_ranks_by_total_actions_across_models(boutique):
    active = _staff(boutique, "active@test.com")
    quiet = _staff(boutique, "quiet@test.com")
    _sale(boutique, active, n=3)
    _sale(boutique, quiet, n=1)

    ranking = _top_active_users(10)
    by_email = {row["user"].email: row["count"] for row in ranking}
    assert by_email["active@test.com"] == 3
    assert by_email["quiet@test.com"] == 1
    assert [row["user"].email for row in ranking][0] == "active@test.com"


def test_counts_several_kinds_of_actions(boutique):
    user = _staff(boutique, "polyvalent@test.com")
    _sale(boutique, user, n=1)   # 1 vente
    Invoice.objects.create(
        boutique=boutique, type=Invoice.DEVIS, status=Invoice.BROUILLON,
        number="D-1", total_ttc=Decimal("0"), currency="XOF", created_by=user,
    )
    product = ProductFactory(compte=boutique.compte)
    movement = stock_services.apply_movement(
        boutique=boutique, product=product, type=StockMovement.ENTREE,
        quantity=Decimal("5"), created_by=user, reason="test",
    )
    invoice = Invoice.objects.create(
        boutique=boutique, type=Invoice.FACTURE, status=Invoice.VALIDEE,
        number="F-1", total_ttc=Decimal("500"), currency="XOF", created_by=user,
    )
    sales_services.record_payment(invoice, amount=Decimal("500"), method=Payment.ESPECES, created_by=user)

    ranking = _top_active_users(10)
    row = next(r for r in ranking if r["user"].email == "polyvalent@test.com")
    assert row["count"] == 5   # vente + devis + facture + mouvement de stock + paiement
    assert movement.created_by == user   # sanity


def test_limit_is_respected(boutique):
    for i in range(12):
        user = _staff(boutique, f"u{i}@test.com")
        _sale(boutique, user, n=i + 1)   # comptes tous différents, aucune égalité
    assert len(_top_active_users(10)) == 10
    top = _top_active_users(10)
    assert top[0]["user"].email == "u11@test.com"   # le plus actif (12 ventes)


def test_users_without_activity_are_excluded(boutique):
    _staff(boutique, "jamais@test.com")
    assert _top_active_users(10) == []


def test_ranking_reports_company_names(boutique):
    user = _staff(boutique, "actif@test.com")
    _sale(boutique, user)
    ranking = _top_active_users(10)
    assert ranking[0]["company_names"] == [boutique.compte.name]


def test_deleted_user_does_not_crash_ranking(boutique):
    """created_by est SET_NULL à la suppression d'un utilisateur : les
    actions restent (anonymes), rien à classer pour lui."""
    user = _staff(boutique, "supprime@test.com")
    _sale(boutique, user)
    user.delete()
    assert _top_active_users(10) == []


# --- vue / permissions -------------------------------------------------------

def test_page_reserved_to_platform_superuser(client, boutique):
    admin = _staff(boutique, "admin-compte@test.com", role=Membership.ADMIN_COMPTE)
    client.force_login(admin)
    response = client.get(reverse("platform_admin:top_users"))
    assert response.status_code == 302 and response.url == reverse("core:home")


def test_page_accessible_to_superuser(client, boutique):
    superuser = User.objects.create_superuser(email="root@test.com", password="x")
    active = _staff(boutique, "vedette@test.com")
    _sale(boutique, active, n=2)
    client.force_login(superuser)
    html = client.get(reverse("platform_admin:top_users")).content.decode()
    assert "vedette@test.com" in html and "Top 10" in html


def test_anonymous_is_redirected(client):
    response = client.get(reverse("platform_admin:top_users"))
    assert response.status_code == 302


def test_hidden_from_navigation_for_non_superuser(client, boutique):
    admin = _staff(boutique, "admin2@test.com", role=Membership.ADMIN_COMPTE)
    client.force_login(admin)
    html = client.get(reverse("core:home")).content.decode()
    assert reverse("platform_admin:top_users") not in html


@pytest.mark.django_db
def test_unavailable_offline(client, settings, boutique):
    from apps.sync.models import DeviceActivation

    settings.IS_OFFLINE = True
    superuser = User.objects.create_superuser(email="root2@test.com", password="x")
    Membership.objects.create(user=superuser, boutique=boutique, role=Membership.ADMIN_COMPTE, is_active=True)
    DeviceActivation.objects.create(
        boutique_id=boutique.id, boutique_name=boutique.name,
        compte_id=boutique.compte_id, compte_name=boutique.compte.name, token="x",
    )
    client.force_login(superuser)
    response = client.get(reverse("platform_admin:top_users"))
    assert response.status_code == 302 and response.url == reverse("core:home")
