"""Blocage d'accès quand l'abonnement est expiré, et limites de plan
(nombre de boutiques/employés)."""

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.sync.tests.factories import BoutiqueFactory
from apps.tenants.limits import boutiques_limit_reached, users_limit_reached
from apps.tenants.models import Membership, Plan, Subscription

pytestmark = pytest.mark.django_db


def _user(boutique, role=Membership.ADMIN_COMPTE, email="u@test.com"):
    user = User.objects.create_user(email=email, password="x")
    Membership.objects.create(user=user, boutique=boutique, role=role, is_active=True)
    return user


def _plan(**kwargs):
    kwargs.setdefault("name", "Test")
    return Plan.objects.create(**kwargs)


def _subscribe(compte, plan, expires_at="unset"):
    kwargs = {"compte": compte, "plan": plan}
    if expires_at != "unset":
        kwargs["expires_at"] = expires_at
    return Subscription.objects.create(**kwargs)


# --- blocage d'accès --------------------------------------------------------

def test_expired_subscription_blocks_a_regular_page(client):
    boutique = BoutiqueFactory()
    user = _user(boutique, Membership.CAISSIER)
    _subscribe(boutique.compte, _plan(), expires_at=timezone.localdate() - timedelta(days=1))
    client.force_login(user)
    response = client.get(reverse("core:home"))
    assert response.status_code == 302 and response.url == reverse("tenants:subscription_expired")


def test_active_subscription_is_not_blocked(client):
    boutique = BoutiqueFactory()
    user = _user(boutique)
    _subscribe(boutique.compte, _plan(), expires_at=timezone.localdate() + timedelta(days=1))
    client.force_login(user)
    assert client.get(reverse("core:home")).status_code == 200


def test_no_subscription_at_all_is_not_blocked(client):
    """Un compte sans ligne Subscription (créé autrement qu'via l'inscription
    normale) n'est pas bloqué par prudence."""
    boutique = BoutiqueFactory()
    user = _user(boutique)
    client.force_login(user)
    assert client.get(reverse("core:home")).status_code == 200


def test_subscription_without_expiry_never_blocks(client):
    boutique = BoutiqueFactory()
    user = _user(boutique)
    _subscribe(boutique.compte, _plan())   # expires_at vide = sans limite de durée
    client.force_login(user)
    assert client.get(reverse("core:home")).status_code == 200


def test_expired_subscription_page_itself_stays_reachable(client):
    boutique = BoutiqueFactory()
    admin = _user(boutique, Membership.ADMIN_COMPTE)
    _subscribe(boutique.compte, _plan(), expires_at=timezone.localdate() - timedelta(days=1))
    client.force_login(admin)
    assert client.get(reverse("tenants:subscription_expired")).status_code == 200
    assert client.get(reverse("tenants:subscription")).status_code == 200


def test_expired_page_shows_renew_link_only_to_admin(client):
    boutique = BoutiqueFactory()
    admin = _user(boutique, Membership.ADMIN_COMPTE, "admin@test.com")
    cashier = _user(boutique, Membership.CAISSIER, "cash@test.com")
    _subscribe(boutique.compte, _plan(), expires_at=timezone.localdate() - timedelta(days=1))

    client.force_login(admin)
    admin_html = client.get(reverse("tenants:subscription_expired")).content.decode()
    assert reverse("tenants:subscription") in admin_html

    client.force_login(cashier)
    cashier_html = client.get(reverse("tenants:subscription_expired")).content.decode()
    assert "Contactez l'administrateur" in cashier_html


def test_superuser_is_never_blocked(client):
    boutique = BoutiqueFactory()
    superuser = User.objects.create_superuser(email="root@test.com", password="x")
    Membership.objects.create(user=superuser, boutique=boutique, role=Membership.ADMIN_COMPTE, is_active=True)
    _subscribe(boutique.compte, _plan(), expires_at=timezone.localdate() - timedelta(days=1))
    client.force_login(superuser)
    assert client.get(reverse("core:home")).status_code == 200


def test_offline_workstation_ignores_expired_subscription(client, settings):
    settings.IS_OFFLINE = True
    boutique = BoutiqueFactory()
    user = _user(boutique)
    _subscribe(boutique.compte, _plan(), expires_at=timezone.localdate() - timedelta(days=1))
    from apps.sync.models import DeviceActivation

    DeviceActivation.objects.create(
        boutique_id=boutique.id, boutique_name=boutique.name,
        compte_id=boutique.compte_id, compte_name=boutique.compte.name, token="x",
    )
    client.force_login(user)
    assert client.get(reverse("core:home")).status_code == 200


def test_expired_subscription_still_allows_sync_api(client):
    boutique = BoutiqueFactory()
    user = _user(boutique)
    _subscribe(boutique.compte, _plan(), expires_at=timezone.localdate() - timedelta(days=1))
    client.force_login(user)
    # Non authentifié par jeton donc 401/403 attendu, jamais une redirection
    # vers subscription_expired : l'API de synchro reste exemptée.
    response = client.get("/api/v1/sync/pull/catalog/products/")
    assert response.status_code in (401, 403)


# --- limites de plan ---------------------------------------------------------

def test_boutiques_limit_reached_helper():
    boutique = BoutiqueFactory()
    plan = _plan(max_boutiques=1)
    _subscribe(boutique.compte, plan)
    assert boutiques_limit_reached(boutique.compte) is True
    plan.max_boutiques = None
    plan.save()
    assert boutiques_limit_reached(boutique.compte) is False


def test_boutiques_limit_ignores_inactive_boutiques():
    boutique = BoutiqueFactory()
    boutique.is_active = False
    boutique.save()
    plan = _plan(max_boutiques=1)
    _subscribe(boutique.compte, plan)
    assert boutiques_limit_reached(boutique.compte) is False


def test_users_limit_counts_distinct_users_across_boutiques():
    boutique = BoutiqueFactory()
    other_boutique = BoutiqueFactory(compte=boutique.compte)
    admin = _user(boutique, Membership.ADMIN_COMPTE, "admin@test.com")
    Membership.objects.create(user=admin, boutique=other_boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    plan = _plan(max_users=1)
    _subscribe(boutique.compte, plan)
    assert users_limit_reached(boutique.compte) is True   # 1 seul utilisateur, sur 2 boutiques


def test_boutique_create_blocked_at_limit(client):
    boutique = BoutiqueFactory()
    admin = _user(boutique, Membership.ADMIN_COMPTE)
    _subscribe(boutique.compte, _plan(max_boutiques=1))
    client.force_login(admin)
    response = client.post(reverse("tenants:boutique_create"), {
        "name": "Deuxième", "code": "DX2", "devise": "XOF",
    })
    assert response.status_code == 302 and response.url == reverse("tenants:subscription")
    from apps.tenants.models import Boutique

    assert Boutique.objects.filter(compte=boutique.compte).count() == 1


def test_boutique_create_allowed_under_limit(client):
    boutique = BoutiqueFactory()
    admin = _user(boutique, Membership.ADMIN_COMPTE)
    _subscribe(boutique.compte, _plan(max_boutiques=3))
    client.force_login(admin)
    response = client.post(reverse("tenants:boutique_create"), {
        "name": "Deuxième", "code": "DX2", "devise": "XOF",
    })
    assert response.status_code == 302 and response.url == reverse("tenants:company_settings")


def test_staff_create_blocked_at_limit(client):
    boutique = BoutiqueFactory()
    admin = _user(boutique, Membership.ADMIN_COMPTE)
    _subscribe(boutique.compte, _plan(max_users=1))
    client.force_login(admin)
    response = client.post(reverse("tenants:staff_create"), {
        "email": "nouveau@test.com", "password": "motdepasse123", "first_name": "N", "last_name": "N",
        "boutique": str(boutique.id), "role": Membership.CAISSIER,
    })
    assert response.status_code == 302 and response.url == reverse("tenants:subscription")
    assert not User.objects.filter(email="nouveau@test.com").exists()
