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

LANGUAGE_CODE = "fr-fr"
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
