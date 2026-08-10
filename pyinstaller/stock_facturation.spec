# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller pour le poste offline (--onedir, fenêtré). Lancer via
scripts/build_exe.ps1, ou directement :
    venv\\Scripts\\pyinstaller.exe pyinstaller\\stock_facturation.spec --noconfirm
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 — injecté par PyInstaller

# `pyinstaller.exe` n'a pas forcément la racine du projet sur sys.path au
# moment où cette spec s'exécute — sans ça, `collect_submodules("config", ...)`
# et `collect_submodules("apps.xxx", ...)` échouent silencieusement (import
# introuvable, `on_error` par défaut avale l'erreur) et renvoient une liste
# vide, comme observé lors d'un premier essai de cette spec.
sys.path.insert(0, str(ROOT))

LOCAL_APPS = ["core", "accounts", "tenants", "catalog", "stock", "sales", "sync"]
# Apps contrib Django avec un paquet de migrations réel (messages/staticfiles
# n'en ont pas — inutile de les lister).
CONTRIB_APPS_WITH_MIGRATIONS = ["admin", "auth", "contenttypes", "sessions"]


def _not_tests(name):
    return "tests" not in name.split(".")


# Django charge views.py/urls.py/serializers.py/forms.py/admin.py/les
# migrations de chaque app dynamiquement (ROOT_URLCONF, include(), gestion
# des migrations...) — jamais via un `import` direct que l'analyse statique
# de PyInstaller pourrait suivre. Même chose pour `config` : seul
# `DJANGO_SETTINGS_MODULE = "config.settings.offline"` (une chaîne) y fait
# référence, aucun `import config...` nulle part. Il faut donc forcer
# l'inclusion du paquet complet de chaque app locale et de `config`, pas
# seulement leurs migrations.
hiddenimports = []
hiddenimports += collect_submodules("config", filter=_not_tests)
for _app in LOCAL_APPS:
    hiddenimports += collect_submodules(f"apps.{_app}", filter=_not_tests)
for _app in CONTRIB_APPS_WITH_MIGRATIONS:
    hiddenimports += collect_submodules(f"django.contrib.{_app}.migrations")
# Paquets tiers : lister juste le nom du paquet n'inclut que son
# __init__.py, pas ses sous-modules (ex: whitenoise.middleware) — même
# subtilité que config/apps ci-dessus, collect_submodules() partout.
hiddenimports += collect_submodules("whitenoise")
hiddenimports += collect_submodules("waitress")
# DRF résout ses classes par défaut (permissions/authentification) via des
# chemins pointés dans REST_FRAMEWORK (config/settings/base.py) — même
# mécanisme dynamique que Django lui-même, il faut donc le paquet complet.
hiddenimports += collect_submodules("rest_framework")
hiddenimports += [
    "webview.platforms.edgechromium",
    "platformdirs",
]

# Django découvre le dossier `templates/` de chaque app via AppConfig.path
# (dérivé du __path__ du module importé) et MigrationLoader liste les
# fichiers de migration sur le disque — les deux cassent silencieusement
# si apps/ et config/ finissent zippés dans le PYZ plutôt qu'en fichiers
# réels. On force donc leur extraction en clair.
module_collection_mode = {"apps": "py", "config": "py"}

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "templates"), "templates"),
    (str(ROOT / "pyinstaller" / "app.ico"), "."),
]
for _app in LOCAL_APPS:
    _tdir = ROOT / "apps" / _app / "templates"
    if _tdir.exists():
        datas.append((str(_tdir), f"apps/{_app}/templates"))

a = Analysis(
    [str(ROOT / "desktop" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    excludes=[],
    module_collection_mode=module_collection_mode,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="stock_facturation",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ROOT / "pyinstaller" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="stock_facturation",
)
