"""Connexion "Se connecter avec Google" — voir apps.accounts.google_oauth et
apps.accounts.views.google_login/google_callback/google_signup_confirm."""

from unittest import mock

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.accounts.views import GOOGLE_OAUTH_STATE_SESSION_KEY, GOOGLE_PENDING_SIGNUP_SESSION_KEY
from apps.accounts import google_oauth
from apps.tenants.models import Compte, Membership

pytestmark = pytest.mark.django_db

CONFIGURED = {"GOOGLE_OAUTH_CLIENT_ID": "id123", "GOOGLE_OAUTH_CLIENT_SECRET": "secret456"}


def _session_state(client):
    return client.session.get(GOOGLE_OAUTH_STATE_SESSION_KEY)


# --- disponibilité -----------------------------------------------------------

def test_is_configured_requires_both_values(settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = ""
    settings.GOOGLE_OAUTH_CLIENT_SECRET = ""
    assert google_oauth.is_configured() is False
    settings.GOOGLE_OAUTH_CLIENT_ID = "id"
    assert google_oauth.is_configured() is False
    settings.GOOGLE_OAUTH_CLIENT_SECRET = "secret"
    assert google_oauth.is_configured() is True


@override_settings(**CONFIGURED)
def test_login_page_shows_google_button_when_configured(client):
    assert "Continuer avec Google" in client.get(reverse("accounts:login")).content.decode()
    assert "Continuer avec Google" in client.get(reverse("tenants:signup")).content.decode()


def test_login_page_hides_google_button_when_not_configured(client, settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = ""
    settings.GOOGLE_OAUTH_CLIENT_SECRET = ""
    assert "Continuer avec Google" not in client.get(reverse("accounts:login")).content.decode()


@override_settings(IS_OFFLINE=True, **CONFIGURED)
def test_google_button_hidden_offline_even_if_configured(client):
    assert "Continuer avec Google" not in client.get(reverse("accounts:login")).content.decode()


# --- google_login --------------------------------------------------------------

def test_login_refused_when_not_configured(client):
    response = client.get(reverse("accounts:google_login"))
    assert response.status_code == 302 and response.url == reverse("accounts:login")


@override_settings(**CONFIGURED)
def test_login_redirects_to_google_with_state(client):
    response = client.get(reverse("accounts:google_login"))
    assert response.status_code == 302
    assert response.url.startswith(google_oauth.AUTHORIZATION_URL)
    assert "client_id=id123" in response.url
    state = _session_state(client)
    assert state and state in response.url


@override_settings(IS_OFFLINE=True, **CONFIGURED)
def test_login_refused_offline(client):
    # Le middleware offline bloque déjà tout sans activation (voir
    # apps.core.middleware) avant même que cette vue s'exécute ; avec ou
    # sans activation, jamais de redirection vers Google.
    response = client.get(reverse("accounts:google_login"))
    assert response.status_code == 302
    assert google_oauth.AUTHORIZATION_URL not in response.url


# --- google_callback -----------------------------------------------------------

def _init_state(client):
    session = client.session
    session[GOOGLE_OAUTH_STATE_SESSION_KEY] = "abc"
    session.save()


@override_settings(**CONFIGURED)
def test_callback_rejects_state_mismatch(client):
    _init_state(client)
    response = client.get(reverse("accounts:google_callback"), {"state": "wrong", "code": "x"})
    assert response.status_code == 302 and response.url == reverse("accounts:login")


@override_settings(**CONFIGURED)
def test_callback_handles_user_cancelling(client):
    _init_state(client)
    response = client.get(reverse("accounts:google_callback"), {"state": "abc", "error": "access_denied"})
    assert response.status_code == 302 and response.url == reverse("accounts:login")


@override_settings(**CONFIGURED)
def test_callback_missing_code(client):
    _init_state(client)
    response = client.get(reverse("accounts:google_callback"), {"state": "abc"})
    assert response.status_code == 302 and response.url == reverse("accounts:login")


@override_settings(**CONFIGURED)
def test_callback_google_error_is_shown(client):
    _init_state(client)
    with mock.patch("apps.accounts.google_oauth.exchange_code", side_effect=google_oauth.GoogleAuthError("boom")):
        response = client.get(reverse("accounts:google_callback"), {"state": "abc", "code": "x"})
    assert response.status_code == 302 and response.url == reverse("accounts:login")


@override_settings(**CONFIGURED)
def test_callback_rejects_unverified_email(client):
    _init_state(client)
    with mock.patch("apps.accounts.google_oauth.exchange_code", return_value={"access_token": "t"}), \
         mock.patch("apps.accounts.google_oauth.fetch_userinfo", return_value={"email": "a@test.com", "email_verified": False}):
        response = client.get(reverse("accounts:google_callback"), {"state": "abc", "code": "x"})
    assert response.status_code == 302 and response.url == reverse("accounts:login")
    assert not User.objects.exists()


@override_settings(**CONFIGURED)
def test_callback_logs_in_existing_user(client, django_user_model):
    _init_state(client)
    user = django_user_model.objects.create_user(email="deja@test.com", password="x")
    with mock.patch("apps.accounts.google_oauth.exchange_code", return_value={"access_token": "t"}), \
         mock.patch("apps.accounts.google_oauth.fetch_userinfo",
                    return_value={"email": "DEJA@test.com", "email_verified": True, "name": "Déjà"}):
        response = client.get(reverse("accounts:google_callback"), {"state": "abc", "code": "x"})
    assert response.status_code == 302 and response.url == reverse("core:home")
    assert client.session["_auth_user_id"] == str(user.pk)


@override_settings(**CONFIGURED)
def test_callback_sends_new_user_to_signup_confirm(client):
    _init_state(client)
    with mock.patch("apps.accounts.google_oauth.exchange_code", return_value={"access_token": "t"}), \
         mock.patch("apps.accounts.google_oauth.fetch_userinfo",
                    return_value={"email": "nouveau@test.com", "email_verified": "true", "name": "Nouveau"}):
        response = client.get(reverse("accounts:google_callback"), {"state": "abc", "code": "x"})
    assert response.status_code == 302 and response.url == reverse("accounts:google_signup_confirm")
    assert client.session[GOOGLE_PENDING_SIGNUP_SESSION_KEY]["email"] == "nouveau@test.com"
    assert not User.objects.exists()   # rien créé avant confirmation


# --- google_signup_confirm ------------------------------------------------------

def _with_pending(client, email="nouveau@test.com", name="Nouveau"):
    session = client.session
    session[GOOGLE_PENDING_SIGNUP_SESSION_KEY] = {"email": email, "name": name}
    session.save()


def test_confirm_without_pending_redirects_to_login(client):
    response = client.get(reverse("accounts:google_signup_confirm"))
    assert response.status_code == 302 and response.url == reverse("accounts:login")


def test_confirm_get_prefills_suggested_name(client):
    _with_pending(client)
    html = client.get(reverse("accounts:google_signup_confirm")).content.decode()
    assert "Nouveau" in html and "nouveau@test.com" in html


def test_confirm_post_creates_company_and_logs_in(client):
    _with_pending(client)
    response = client.post(reverse("accounts:google_signup_confirm"), {
        "entreprise_name": "Ma Boite", "boutique_name": "Boutique A", "devise": "XOF",
    })
    assert response.status_code == 302 and response.url == reverse("core:home")

    user = User.objects.get(email="nouveau@test.com")
    assert not user.has_usable_password()
    membership = Membership.objects.get(user=user)
    assert membership.role == Membership.ADMIN_COMPTE
    assert membership.boutique.name == "Boutique A" and membership.boutique.is_default
    assert Compte.objects.get(pk=membership.boutique.compte_id).name == "Ma Boite"
    assert GOOGLE_PENDING_SIGNUP_SESSION_KEY not in client.session


def test_confirm_rejects_email_taken_meanwhile(client, django_user_model):
    _with_pending(client, email="course@test.com")
    django_user_model.objects.create_user(email="course@test.com", password="x")
    response = client.post(reverse("accounts:google_signup_confirm"), {
        "entreprise_name": "Ma Boite", "boutique_name": "Boutique A", "devise": "XOF",
    })
    assert response.status_code == 200   # réaffiche le formulaire, pas de redirection
    assert Compte.objects.count() == 0


# --- service partagé -----------------------------------------------------------

def test_create_company_with_owner_sets_unusable_password_by_default():
    from apps.tenants.services import create_company_with_owner

    user, boutique = create_company_with_owner(
        user_model=User, entreprise_name="Sans Mdp", boutique_name="B1", devise="XOF", email="x@test.com",
    )
    assert not user.has_usable_password()
    assert boutique.is_default and boutique.compte.name == "Sans Mdp"
    assert Membership.objects.get(user=user, boutique=boutique).role == Membership.ADMIN_COMPTE


def test_signup_form_still_works_end_to_end(client):
    """Non-régression : l'inscription classique (email + mot de passe) passe
    toujours par le même service partagé et fonctionne à l'identique."""
    response = client.post(reverse("tenants:signup"), {
        "entreprise_name": "Classique SA", "boutique_name": "Boutique", "devise": "XOF",
        "email": "classique@test.com", "password": "un-mot-de-passe-solide-42", "password_confirm": "un-mot-de-passe-solide-42",
    })
    assert response.status_code == 302 and response.url == reverse("core:home")
    user = User.objects.get(email="classique@test.com")
    assert user.has_usable_password()
