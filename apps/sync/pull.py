from .activation import get_active_client
from .models import DeviceActivation, SyncCursor


def _pull_boutique(client):
    """Cas particulier : endpoint singulier (pas de pagination). Tiré en
    premier — les autres ressources ont besoin du Compte/Boutique locaux
    déjà en base pour satisfaire leurs FK."""
    from apps.tenants.models import Boutique, Compte

    data = client.get("pull/tenants/boutique/")
    Compte.objects.update_or_create(id=data["compte_id"], defaults={"name": data["compte_name"]})
    Boutique.objects.update_or_create(
        id=data["id"],
        defaults={
            "compte_id": data["compte_id"],
            "name": data["name"],
            "code": data["code"],
            "address": data.get("address", ""),
            "phone": data.get("phone", ""),
            "devise": data["devise"],
            "country_calling_code": data.get("country_calling_code", ""),
            "om_account_name": data.get("om_account_name", ""),
            "om_number": data.get("om_number", ""),
            "momo_account_name": data.get("momo_account_name", ""),
            "momo_number": data.get("momo_number", ""),
            "is_default": data.get("is_default", True),
        },
    )
    return data


def _pull_paginated_resource(client, path, since):
    """Tire toutes les pages d'une ressource (suit `next`), renvoie
    `(items, server_time)` — `server_time` pris sur la PREMIÈRE page
    seulement (photo à un instant T, jamais l'horloge locale)."""

    items = []
    server_time = None
    params = {"since": since.isoformat()} if since else None
    url = path
    while url:
        data = client.get(url, params=params)
        params = None  # déjà encodés dans le lien "next" des pages suivantes
        items.extend(data["results"])
        if server_time is None:
            server_time = data.get("server_time")
        url = data.get("next")
    return items, server_time


def _upsert_tax_rate(items, compte_id):
    from apps.sales.models import TaxRate

    for item in items:
        TaxRate.objects.update_or_create(
            id=item["id"],
            defaults={
                "compte_id": compte_id, "name": item["name"], "rate": item["rate"],
                "is_default": item["is_default"], "active_from": item["active_from"],
            },
        )


def _upsert_category(items, compte_id):
    from apps.catalog.models import Category

    for item in items:
        Category.objects.update_or_create(
            id=item["id"], defaults={"compte_id": compte_id, "name": item["name"]},
        )
    # Second passage : le parent peut apparaître après l'enfant dans la
    # page tirée, on ne peut donc pas fiabiliser le lien en un seul passage.
    for item in items:
        if item.get("parent_id"):
            Category.objects.filter(id=item["id"]).update(parent_id=item["parent_id"])


def _upsert_unit(items, compte_id):
    from apps.catalog.models import Unit

    for item in items:
        Unit.objects.update_or_create(
            id=item["id"],
            defaults={"compte_id": compte_id, "name": item["name"], "symbol": item.get("symbol", "")},
        )


def _upsert_exchange_rate(items, boutique_id):
    from apps.tenants.models import ExchangeRate

    for item in items:
        ExchangeRate.objects.update_or_create(
            id=item["id"],
            defaults={"boutique_id": boutique_id, "currency": item["currency"], "rate": item["rate"]},
        )


def _upsert_user(items, compte_id):
    from apps.accounts.models import User

    for item in items:
        User.objects.update_or_create(
            id=item["id"],
            defaults={
                "email": item["email"], "password": item["password"],
                "first_name": item.get("first_name", ""), "last_name": item.get("last_name", ""),
                "is_active": item.get("is_active", True),
            },
        )


def _upsert_membership(items, boutique_id):
    from apps.tenants.models import Membership

    for item in items:
        Membership.objects.update_or_create(
            id=item["id"],
            defaults={
                "user_id": item["user_id"], "boutique_id": boutique_id,
                "role": item["role"], "is_active": item["is_active"],
            },
        )


def _upsert_product(items, compte_id):
    # Les photos ne sont pas rapatriées dans cet incrément — seul
    # `image_url` (lien vers le fichier côté serveur) est reçu, le champ
    # local Product.image reste vide (placeholder à l'affichage). Ne
    # bloque pas la vente hors-ligne, traité comme une lacune connue.
    from apps.catalog.models import Product

    for item in items:
        Product.objects.update_or_create(
            id=item["id"],
            defaults={
                "compte_id": compte_id,
                "category_id": item.get("category_id"),
                "name": item["name"],
                "sku": item.get("sku", ""),
                "barcode": item.get("barcode", ""),
                "unit_id": item["unit_id"],
                "default_sale_price": item["default_sale_price"],
                "tva_rate": item.get("tva_rate", 0),
                "low_stock_threshold_default": item.get("low_stock_threshold_default", 5),
                "is_active": item.get("is_active", True),
            },
        )


def _upsert_stock_level(items, boutique_id):
    from apps.stock.models import StockLevel

    for item in items:
        StockLevel.objects.update_or_create(
            boutique_id=boutique_id, product_id=item["product_id"],
            defaults={"quantity": item["quantity"]},
        )


def _upsert_product_boutique_price(items, boutique_id):
    from apps.catalog.models import ProductBoutiquePrice

    for item in items:
        ProductBoutiquePrice.objects.update_or_create(
            id=item["id"],
            defaults={
                "product_id": item["product_id"], "boutique_id": boutique_id,
                "price_override": item.get("price_override"),
                "low_stock_threshold_override": item.get("low_stock_threshold_override"),
                "is_available": item.get("is_available", True),
            },
        )


# Ordre de dépendance : tax_rates/categories/units/exchange_rates/users
# n'ont besoin que du Compte/Boutique (déjà tirés par _pull_boutique) ;
# memberships a besoin de users ; products a besoin de categories/units ;
# product_boutique_prices a besoin de products.
PULL_RESOURCES = [
    ("tax_rates", "pull/sales/tax-rates/", _upsert_tax_rate, "compte"),
    ("categories", "pull/catalog/categories/", _upsert_category, "compte"),
    ("units", "pull/catalog/units/", _upsert_unit, "compte"),
    ("exchange_rates", "pull/tenants/exchange-rates/", _upsert_exchange_rate, "boutique"),
    ("users", "pull/accounts/users/", _upsert_user, "compte"),
    ("memberships", "pull/tenants/memberships/", _upsert_membership, "boutique"),
    ("products", "pull/catalog/products/", _upsert_product, "compte"),
    ("product_boutique_prices", "pull/catalog/product-boutique-prices/", _upsert_product_boutique_price, "boutique"),
    ("stock_levels", "pull/stock/levels/", _upsert_stock_level, "boutique"),
]


def run_pull_cycle(client=None):
    client = client or get_active_client()
    _pull_boutique(client)

    activation = DeviceActivation.get_active()
    scope_ids = {"compte": activation.compte_id, "boutique": activation.boutique_id}

    summary = {"boutique": 1}
    for resource, path, upsert_fn, scope in PULL_RESOURCES:
        cursor = SyncCursor.objects.filter(resource=resource).first()
        since = cursor.last_synced_at if cursor else None
        items, server_time = _pull_paginated_resource(client, path, since)
        upsert_fn(items, scope_ids[scope])
        if server_time:
            SyncCursor.objects.update_or_create(resource=resource, defaults={"last_synced_at": server_time})
        summary[resource] = len(items)
    return summary
