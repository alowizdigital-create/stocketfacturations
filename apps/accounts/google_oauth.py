"""Connexion "Se connecter avec Google" — flux OAuth 2.0 "Authorization
Code" implémenté à la main, dans le même esprit que apps.sales.whatsapp_api :
quelques appels HTTP directs à Google plutôt qu'une dépendance
(django-allauth) dont on n'utiliserait qu'une fraction. Gratuit côté Google,
sans limite de comptes ni vérification requise pour ces scopes de base
(email/profil, rien de sensible).

Configuré via GOOGLE_OAUTH_CLIENT_ID/SECRET (voir .env.example) — tant
qu'ils sont vides, is_configured() est faux et le bouton ne s'affiche nulle
part (voir apps.core.context_processors.google_auth)."""

import logging
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.urls import reverse

logger = logging.getLogger(__name__)

AUTHORIZATION_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


class GoogleAuthError(Exception):
    """Levée si l'échange avec Google échoue ou renvoie une réponse
    inexploitable — toujours affichée à l'utilisateur sous une forme
    générique (voir apps.accounts.views.google_callback), le détail reste
    dans les logs."""


def is_configured():
    return bool(settings.GOOGLE_OAUTH_CLIENT_ID and settings.GOOGLE_OAUTH_CLIENT_SECRET)


def redirect_uri(request):
    """URL de retour après connexion Google — doit être déclarée à
    l'identique dans les identifiants OAuth du projet Google Cloud (schéma,
    domaine et chemin exacts, port y compris en local)."""
    return request.build_absolute_uri(reverse("accounts:google_callback"))


def build_authorization_url(request, state):
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        # Réaffiche le sélecteur de compte Google à chaque tentative : évite
        # qu'un poste partagé (boutique) reste connecté au compte Google du
        # précédent utilisateur sans qu'on s'en rende compte.
        "prompt": "select_account",
    }
    return f"{AUTHORIZATION_URL}?{urlencode(params)}"


def exchange_code(request, code):
    """Échange le code d'autorisation contre un jeton d'accès. Renvoie le
    JSON de Google (contient notamment `access_token`)."""
    try:
        response = requests.post(
            TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": redirect_uri(request),
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Échec de l'échange du code d'autorisation Google : %s", exc)
        raise GoogleAuthError("Impossible de contacter Google.") from exc
    data = response.json()
    if "access_token" not in data:
        logger.error("Réponse Google sans access_token : %s", data)
        raise GoogleAuthError("Réponse inattendue de Google.")
    return data


def fetch_userinfo(access_token):
    """Profil de l'utilisateur Google connecté — email, email_verified,
    name. Ne contient jamais de mot de passe ni de jeton réutilisable une
    fois cet appel terminé : rien de tout ça n'est stocké."""
    try:
        response = requests.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Échec de la récupération du profil Google : %s", exc)
        raise GoogleAuthError("Impossible de récupérer votre profil Google.") from exc
    return response.json()
