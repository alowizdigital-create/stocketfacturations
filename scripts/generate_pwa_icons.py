"""Génère les icônes PWA (static/icons/) à partir du monogramme de
l'application, dans les couleurs de la marque. À relancer si le logo
change — aucune dépendance à un fichier source, tout est dessiné."""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

BRAND = (111, 74, 142)  # #6f4a8e
BRAND_DARK = (85, 54, 111)  # #55366f

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "icons")


def _font(size):
    candidates = [
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def make_icon(size, *, padding_ratio=0.0, filename, maskable=False):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if maskable:
        # Icône "maskable" : le système applique lui-même sa propre forme
        # (cercle, squircle...) — le fond doit couvrir tout le canevas
        # sans transparence, sinon des coins vides peuvent apparaître.
        draw.rectangle([0, 0, size - 1, size - 1], fill=BRAND)
    else:
        radius = int(size * 0.22)
        draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=BRAND)

    # Monogramme "SF" centré, dans la zone sûre (utile pour les icônes
    # "maskable" recadrées en cercle par Android).
    safe = size * (1 - padding_ratio * 2)
    text = "SF"
    font_size = int(safe * 0.46)
    font = _font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) / 2 - bbox[0]
    y = (size - th) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    img.save(os.path.join(OUTPUT_DIR, filename))
    print("wrote", filename)


def make_apple_touch_icon(size=180, filename="apple-touch-icon.png"):
    # iOS n'accepte pas la transparence et arrondit lui-même les coins :
    # fond plein, pas de rounded_rectangle.
    img = Image.new("RGB", (size, size), BRAND)
    draw = ImageDraw.Draw(img)
    font = _font(int(size * 0.44))
    text = "SF"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) / 2 - bbox[0]
    y = (size - th) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=(255, 255, 255))
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    img.save(os.path.join(OUTPUT_DIR, filename))
    print("wrote", filename)


if __name__ == "__main__":
    make_icon(192, padding_ratio=0.05, filename="icon-192.png")
    make_icon(512, padding_ratio=0.05, filename="icon-512.png")
    make_icon(192, padding_ratio=0.20, filename="icon-192-maskable.png", maskable=True)
    make_icon(512, padding_ratio=0.20, filename="icon-512-maskable.png", maskable=True)
    make_apple_touch_icon()
    print("done")
