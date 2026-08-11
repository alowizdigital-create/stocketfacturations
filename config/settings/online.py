"""
Serveur en ligne, déployé via Docker sur le VPS Hostinger (zweey.com).
"""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = env.bool("DJANGO_DEBUG", default=False)

ALLOWED_HOSTS = env.list(
    "DJANGO_ALLOWED_HOSTS",
    default=["zweey.com", "www.zweey.com", "api.zweey.com"],
)

CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["https://zweey.com", "https://www.zweey.com", "https://api.zweey.com"],
)

# BASE_DIR est déjà correctement défini par base.py (from .base import *) —
# ne JAMAIS le redéfinir ici : une redéfinition avec un niveau de dossier
# en moins (bug précédent) fait pointer tous les chemins dérivés
# (MEDIA_ROOT, apps.core.views.service_worker...) vers config/ au lieu de
# la racine du projet, en cassant silencieusement en production.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"  # noqa: F405

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': env('DB_NAME', default='zweeydb'),
        'USER': env('DB_USER', default='zweeyuser'),
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST', default='stocketfacturation-zweeydatabase-pss441'),
        'PORT': env('DB_PORT', default='3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    *[m for m in MIDDLEWARE if m != "django.middleware.security.SecurityMiddleware"],  # noqa: F405
]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}



SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
