import io
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.catalog.models import Product
from apps.sync import outbox as outbox_module
from apps.sync import pull as pull_module

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _cleanup_media_files():
    """Ces tests écrivent de vrais fichiers dans MEDIA_ROOT (pas un
    répertoire temporaire dédié — le rollback transactionnel de la base de
    test ne s'applique qu'à la DB, jamais au stockage fichier). Sans ce
    nettoyage, chaque run laisserait des photos de test dans le vrai
    dossier media/produits/ du projet."""
    produits_dir = os.path.join(settings.MEDIA_ROOT, "produits")
    before = set(os.listdir(produits_dir)) if os.path.isdir(produits_dir) else set()
    yield
    after = set(os.listdir(produits_dir)) if os.path.isdir(produits_dir) else set()
    for name in after - before:
        try:
            os.remove(os.path.join(produits_dir, name))
        except OSError:
            pass


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buf, format="PNG")
    return buf.getvalue()


# --- PushProductImageView (offline -> online) ------------------------------


def test_push_product_image_saves_file_on_product(api_client, boutique, product):
    url = f"/api/v1/sync/push/products/{product.id}/image/"
    upload = SimpleUploadedFile("photo.png", _png_bytes(), content_type="image/png")

    resp = api_client.post(url, {"image": upload}, format="multipart")

    assert resp.status_code == 200
    product.refresh_from_db()
    assert product.image
    assert product.image.name.endswith(".png")


def test_push_product_image_missing_file_is_rejected(api_client, boutique, product):
    url = f"/api/v1/sync/push/products/{product.id}/image/"
    resp = api_client.post(url, {}, format="multipart")
    assert resp.status_code == 400


def test_push_product_image_scoped_to_own_compte(api_client, boutique):
    from .factories import ProductFactory

    other_product = ProductFactory()  # autre compte
    url = f"/api/v1/sync/push/products/{other_product.id}/image/"
    upload = SimpleUploadedFile("photo.png", _png_bytes(), content_type="image/png")

    resp = api_client.post(url, {"image": upload}, format="multipart")

    assert resp.status_code == 404
    other_product.refresh_from_db()
    assert not other_product.image


# --- _push_product_image (déclenchée après passage en SENT) ----------------


def test_outbox_push_product_image_uploads_via_post_file(boutique):
    from .factories import ProductFactory

    product = ProductFactory(compte=boutique.compte)
    product.image.save("local.png", io.BytesIO(_png_bytes()), save=True)

    mock_client = MagicMock()
    outbox_module._push_product_image(product.id, mock_client)

    mock_client.post_file.assert_called_once()
    args, kwargs = mock_client.post_file.call_args
    assert args[0] == f"push/products/{product.id}/image/"
    assert args[1] == "image"


def test_outbox_push_product_image_noop_when_no_local_image(boutique):
    from .factories import ProductFactory

    product = ProductFactory(compte=boutique.compte)
    mock_client = MagicMock()

    outbox_module._push_product_image(product.id, mock_client)

    mock_client.post_file.assert_not_called()


def test_outbox_push_product_image_failure_does_not_raise(boutique):
    from .factories import ProductFactory

    product = ProductFactory(compte=boutique.compte)
    product.image.save("local.png", io.BytesIO(_png_bytes()), save=True)

    mock_client = MagicMock()
    mock_client.post_file.side_effect = ConnectionError("no network")

    outbox_module._push_product_image(product.id, mock_client)  # ne doit jamais lever


# --- _upsert_product (online -> offline) ------------------------------------


def test_upsert_product_downloads_image_when_local_field_empty(boutique):
    item = {
        "id": str(uuid.uuid4()),
        "category_id": None,
        "name": "Produit tiré",
        "sku": "", "barcode": "",
        "unit_id": str(_make_unit(boutique).id),
        "default_sale_price": "1000",
        "image_url": "https://example.com/media/produits/photo.jpg",
    }

    fake_response = MagicMock()
    fake_response.content = _png_bytes()
    fake_response.raise_for_status = MagicMock()

    with patch.object(pull_module.requests, "get", return_value=fake_response) as mock_get:
        pull_module._upsert_product([item], boutique.compte_id)

    mock_get.assert_called_once_with("https://example.com/media/produits/photo.jpg", timeout=15)
    product = Product.objects.get(pk=item["id"])
    assert product.image
    # Le nom de fichier peut être suffixé par le storage en cas de collision
    # avec un fichier déjà présent d'un run précédent — seul le motif du
    # nom d'origine importe ici, pas l'égalité stricte.
    assert "photo" in product.image.name and product.image.name.endswith(".jpg")


def test_upsert_product_does_not_redownload_when_image_already_present(boutique):
    from .factories import ProductFactory

    unit = _make_unit(boutique)
    product = ProductFactory(compte=boutique.compte, unit=unit)
    product.image.save("existing.png", io.BytesIO(_png_bytes()), save=True)

    item = {
        "id": str(product.id),
        "category_id": None,
        "name": product.name,
        "sku": "", "barcode": "",
        "unit_id": str(unit.id),
        "default_sale_price": "1000",
        "image_url": "https://example.com/media/produits/should-not-fetch.jpg",
    }

    with patch.object(pull_module.requests, "get") as mock_get:
        pull_module._upsert_product([item], boutique.compte_id)

    mock_get.assert_not_called()


def test_upsert_product_image_download_failure_does_not_block_metadata_upsert(boutique):
    item = {
        "id": str(uuid.uuid4()),
        "category_id": None,
        "name": "Produit tiré",
        "sku": "", "barcode": "",
        "unit_id": str(_make_unit(boutique).id),
        "default_sale_price": "1000",
        "image_url": "https://example.com/media/produits/photo.jpg",
    }

    with patch.object(pull_module.requests, "get", side_effect=ConnectionError("no network")):
        pull_module._upsert_product([item], boutique.compte_id)  # ne doit jamais lever

    product = Product.objects.get(pk=item["id"])
    assert product.name == "Produit tiré"
    assert not product.image


def _make_unit(boutique):
    from .factories import UnitFactory

    return UnitFactory(compte=boutique.compte)
