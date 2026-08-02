"""
Instance offline embarquée (poste unique par boutique), lancée par
desktop/launcher.py — tourne en local dans la fenêtre pywebview.
"""

import sys

from .base import *  # noqa: F401,F403

DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

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

# Boutique/token d'activation de ce poste offline (renseignés par
# desktop/activation.py au premier lancement, stockés en base locale).
SYNC_BASE_URL = env("SYNC_BASE_URL", default="https://zweey.com")  # noqa: F405
