"""
Settings communs aux modes en ligne, offline et dev.
"""

from pathlib import Path

import environ
from django.contrib.messages import constants as message_constants

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(env_file)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="django-insecure-dev-key-change-me")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.core",
    "apps.accounts",
    "apps.tenants",
    "apps.catalog",
    "apps.stock",
    "apps.sales",
    "apps.sync",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # Doit suivre SessionMiddleware (lit la langue choisie en session) et
    # précéder CommonMiddleware (qui en dépend pour rediriger correctement) —
    # ordre imposé par Django, voir sa documentation i18n.
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.CurrentTenantMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.core.context_processors.is_offline",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Expiration par inactivité (pas depuis la connexion) : SESSION_SAVE_EVERY_REQUEST
# force le rafraîchissement du cookie à chaque requête, donc une session
# active toute une journée ne se coupe jamais en plein milieu — seule une
# session vraiment inutilisée pendant SESSION_COOKIE_AGE expire. Réduit la
# fenêtre d'exposition si une session Windows/navigateur reste ouverte sans
# surveillance (poste partagé en boutique, ex: version offline).
SESSION_COOKIE_AGE = env.int("SESSION_COOKIE_AGE", default=8 * 60 * 60)  # 8h d'inactivité
SESSION_SAVE_EVERY_REQUEST = True

LANGUAGE_CODE = "fr-fr"
# Français = langue source des templates (les chaînes {% trans %}/gettext
# sont écrites en français directement dans le code) — seul un catalogue
# anglais est donc nécessaire (locale/en/LC_MESSAGES/django.po), pas de
# catalogue français. Compilé en .mo via scripts/compile_translations.py
# (outil pure-Python, voir le commentaire dans ce script) plutôt que
# `manage.py compilemessages`, qui nécessite les binaires GNU gettext —
# absents par défaut sur ce poste Windows et non garantis sur l'image
# Docker de prod ni sur la machine de build de l'exe.
LANGUAGES = [("fr", "Français"), ("en", "English")]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = env("DJANGO_TIME_ZONE", default="Africa/Abidjan")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# True uniquement pour config.settings.offline — bascule le middleware sur
# la boutique figée à l'activation plutôt que la résolution par session,
# et active la mise en file d'attente des écritures pour la synchro
# (voir apps.core.middleware.CurrentTenantMiddleware, apps.sync.outbox).
IS_OFFLINE = False

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "accounts:login"

# Le tag Django par défaut pour messages.error() est "error", mais Bootstrap
# n'a pas de classe .alert-error (seulement .alert-danger) — sans ce
# mapping, templates/base.html génère `class="alert alert-error"`, une
# classe qui n'existe pas : le message s'affiche donc sans la couleur rouge
# attendue.
MESSAGE_TAGS = {
    message_constants.ERROR: "danger",
}

# --- Email (mot de passe oublié, notifications futures) -------------------
# Backend console par défaut (affiche l'email dans les logs au lieu de
# l'envoyer) tant qu'aucun serveur SMTP n'est configuré via les variables
# d'environnement ci-dessous — évite un crash en production si l'envoi
# réel n'est pas encore branché, au prix d'un lien de réinitialisation
# invisible pour l'utilisateur (visible uniquement dans les logs du
# conteneur) jusqu'à configuration.
EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@zweey.com")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.sync.authentication.BoutiqueTokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# Devise et TVA par défaut pour les nouvelles boutiques (le taux réel est
# configurable par entreprise via TaxRate — voir apps.sales).
DEFAULT_CURRENCY = "XOF"
DEFAULT_TVA_RATE = env.float("DEFAULT_TVA_RATE", default=18.0)

# --- API WhatsApp (techsoft-sms.com) -------------------------------------
# Documentation publique introuvable pour l'envoi de document par WhatsApp
# au moment de l'implémentation — l'URL/les paramètres ci-dessous sont un
# point de départ à ajuster dans apps/sales/whatsapp_api.py une fois la
# vraie documentation (ou collection Postman) du compte en main.
TECHSOFT_API_KEY = env("TECHSOFT_API_KEY", default="")
TECHSOFT_WHATSAPP_API_URL = env(
    "TECHSOFT_WHATSAPP_API_URL", default="https://app.techsoft-sms.com/whatsapp/api"
)
TECHSOFT_WHATSAPP_SENDER = env("TECHSOFT_WHATSAPP_SENDER", default="")
