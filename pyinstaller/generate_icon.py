"""
Script one-shot : convertit l'icône PWA existante (static/icons/icon-512.png)
en .ico multi-tailles pour l'exe Windows. Résultat committé
(pyinstaller/app.ico) — à relancer seulement si l'icône source change.

Usage : venv\\Scripts\\python.exe pyinstaller\\generate_icon.py
"""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "static" / "icons" / "icon-512.png"
DEST = ROOT / "pyinstaller" / "app.ico"

SIZES = [(16, 16), (32, 32), (48, 48), (256, 256)]


def main():
    if not SOURCE.exists():
        raise SystemExit(f"Introuvable : {SOURCE}")
    img = Image.open(SOURCE).convert("RGBA")
    img.save(DEST, format="ICO", sizes=SIZES)
    print(f"Écrit : {DEST}")


if __name__ == "__main__":
    main()
