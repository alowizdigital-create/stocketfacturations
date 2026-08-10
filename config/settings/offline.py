"""
Instance offline embarquée (poste unique par boutique), lancée par
desktop/launcher.py — tourne en local dans la fenêtre pywebview.
"""

import sys

from .base import *  # noqa: F401,F403

DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
IS_OFFLINE = True

# DEBUG=False désactive le service automatique de /static/ par
# runserver/django.contrib.staticfiles — WhiteNoise le remplace, ici
# comme en ligne (config/settings/online.py). USE_FINDERS sert
# directement depuis STATICFILES_DIRS, sans étape collectstatic.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *[m for m in MIDDLEWARE if m != "django.middleware.security.SecurityMiddleware"],  # noqa: F405
]
WHITENOISE_USE_FINDERS = True

if getattr(sys, "frozen", False):
    # Exe empaqueté par PyInstaller : la base et les logs vivent hors du
    # bundle, dans le répertoire de données utilisateur (jamais dans le
    # dossier temporaire/lecture-seule du bundle).
    import platformdirs

    DATA_DIR = Path(platformdirs.user_data_dir("StockFacturation", "Zweey"))  # noqa: F405
else:
    # Mode dev : on simule l'exe offline sans être packagé.
    DATA_DIR = BASE_DIR / "var" / "offline"  # noqa: F405

DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATA_DIR / "db.sqlite3",
        # Le futur worker de synchro en tâche de fond partagera ce même
        # process SQLite — délai d'attente généreux en prévision plutôt
        # que des "database is locked" occasionnels.
        "OPTIONS": {"timeout": 20},
    }
}

MEDIA_ROOT = DATA_DIR / "media"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": DATA_DIR / "app.log",
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 3,
        },
    },
    "root": {"handlers": ["file"], "level": "INFO"},
}

# Boutique/jeton d'activation de ce poste offline : voir
# apps.sync.models.DeviceActivation (une ligne unique en base locale,
# renseignée par l'écran d'activation — apps.sync.views.activate_device —
# au premier lancement).
SYNC_BASE_URL = env("SYNC_BASE_URL", default="https://zweey.com")  # noqa: F405
