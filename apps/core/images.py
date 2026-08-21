"""Conversion/compression d'images en WebP avant stockage — utilisé par
tous les modèles portant une ImageField (Product, ProductImage, Compte)
via leur save() surchargé, donc systématique quel que soit le point
d'entrée (formulaire web, téléchargement par la synchro offline...).
Best-effort : un fichier illisible par Pillow est conservé tel quel
plutôt que de faire échouer toute la sauvegarde pour une image."""

import io
import logging

from django.core.files.base import ContentFile
from PIL import Image

logger = logging.getLogger(__name__)

WEBP_QUALITY = 82
MAX_DIMENSION = 1600


def convert_image_bytes_to_webp(data, original_name, *, quality=WEBP_QUALITY, max_dimension=MAX_DIMENSION):
    """Renvoie (nouveau_nom, ContentFile) — le contenu en WebP si la
    conversion réussit, sinon les octets d'origine inchangés."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        logger.warning("Image illisible par Pillow (%s), conservée telle quelle.", original_name)
        return original_name, ContentFile(data, name=original_name)

    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

    if max(image.size) > max_dimension:
        scale = max_dimension / max(image.size)
        new_size = (round(image.width * scale), round(image.height * scale))
        image = image.resize(new_size, Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="WEBP", quality=quality, method=6)
    base_name = original_name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    webp_name = f"{base_name}.webp"
    return webp_name, ContentFile(buffer.getvalue(), name=webp_name)


def ensure_webp(field_file):
    """Convertit le contenu d'un FieldFile en WebP et le réassigne — no-op
    si déjà en .webp (évite de ré-encoder à chaque save() du modèle, pas
    seulement quand l'image change réellement)."""
    if not field_file or field_file.name.lower().endswith(".webp"):
        return
    field_file.seek(0)
    data = field_file.read()
    _, content = convert_image_bytes_to_webp(data, field_file.name, quality=WEBP_QUALITY, max_dimension=MAX_DIMENSION)
    field_file.save(content.name, content, save=False)
