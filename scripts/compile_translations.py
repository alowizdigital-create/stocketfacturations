"""Compile locale/en/LC_MESSAGES/django.po -> django.mo.

`manage.py compilemessages` shells out to the GNU gettext `msgfmt` binary,
which isn't installed by default on this Windows dev machine and isn't
guaranteed on the production Docker image or the PyInstaller build machine
either. `polib` compiles the same .po format in pure Python, with no
external binary dependency anywhere in the pipeline.

Usage (after editing the .po file):
    venv\\Scripts\\python.exe scripts\\compile_translations.py

`polib` is a dev-time-only tool (not a runtime dependency — Django only
reads the compiled .mo file, never polib itself): `pip install polib` if
not already present in the venv.
"""

from pathlib import Path

import polib

ROOT = Path(__file__).resolve().parent.parent
PO_PATH = ROOT / "locale" / "en" / "LC_MESSAGES" / "django.po"
MO_PATH = ROOT / "locale" / "en" / "LC_MESSAGES" / "django.mo"


def main():
    po = polib.pofile(str(PO_PATH))
    po.save_as_mofile(str(MO_PATH))
    print(f"Compiled {PO_PATH} -> {MO_PATH} ({len(po)} entries).")


if __name__ == "__main__":
    main()
