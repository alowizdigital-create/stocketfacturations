"""Lecture du catalogue pour la vitrine publique : produits publiés, avec
stock et prix de la boutique, et panier en session."""

from decimal import Decimal

from django.db.models import DecimalField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.catalog.models import Product, ProductBoutiquePrice
from apps.sales.services import _line_totals
from apps.stock.models import StockLevel

CART_SESSION_KEY = "shop_cart"
MAX_QTY_PER_LINE = 99


def published_products(boutique):
    """Produits actifs et publiés de l'entreprise, avec `stock` (quantité
    dans cette boutique). Un produit que la boutique a marqué indisponible
    (ProductBoutiquePrice.is_available=False) est exclu."""
    stock = StockLevel.objects.filter(boutique=boutique, product=OuterRef("pk")).values("quantity")[:1]
    hidden = ProductBoutiquePrice.objects.filter(boutique=boutique, is_available=False).values("product_id")
    return (
        Product.objects.filter(compte=boutique.compte, is_active=True, is_online=True)
        .exclude(pk__in=hidden)
        .annotate(stock=Coalesce(Subquery(stock), Value(0), output_field=DecimalField()))
        .select_related("category", "unit")
    )


def price_overrides(boutique):
    return {
        o.product_id: o.price_override
        for o in ProductBoutiquePrice.objects.filter(boutique=boutique, price_override__isnull=False)
    }


def unit_price_ht(product, overrides):
    return overrides.get(product.id, product.default_sale_price)


def unit_price_ttc(product, overrides):
    return _line_totals(Decimal("1"), unit_price_ht(product, overrides), product.tva_rate)[1]


# En dessous de ce stock, la carte affiche « N restants » avec une jauge.
LOW_STOCK_DISPLAY = 10


def attach_prices(products, boutique):
    """Pose sur chaque produit de la liste : `shop_price` (TTC unitaire),
    `shop_compare_price` (ancien prix TTC barré) et `on_sale` si une
    promotion est en cours (prix avant promotion > prix actuel, non échue),
    `promo_ends_ms` (fin de promo en ms, pour le compte à rebours) et
    `low_stock`/`stock_gauge` (jauge de stock restant)."""
    overrides = price_overrides(boutique)
    now = timezone.now()
    products = list(products)
    for product in products:
        product.shop_price = unit_price_ttc(product, overrides)
        promo_active = product.promo_ends_at is None or product.promo_ends_at > now
        compare = product.compare_at_price
        product.on_sale = bool(compare and promo_active and compare > unit_price_ht(product, overrides))
        product.shop_compare_price = (
            _line_totals(Decimal("1"), compare, product.tva_rate)[1] if product.on_sale else None
        )
        product.discount_percent = (
            int(round((product.shop_compare_price - product.shop_price) * 100 / product.shop_compare_price))
            if product.on_sale and product.shop_compare_price else 0
        )
        product.promo_ends_ms = (
            int(product.promo_ends_at.timestamp() * 1000) if product.on_sale and product.promo_ends_at else None
        )
        stock = int(product.stock)
        product.low_stock = 0 < stock <= LOW_STOCK_DISPLAY
        product.stock_gauge = min(stock * 100 // LOW_STOCK_DISPLAY, 100)
    return products


RELATED_COUNT = 4


def related_products(boutique, product):
    """Suggestions « Vous aimerez aussi » : d'abord la même catégorie, puis
    les autres produits publiés, disponibles en premier, au plus
    RELATED_COUNT. Jamais le produit lui-même."""
    others = published_products(boutique).exclude(pk=product.pk).order_by("-stock", "name")
    chosen = []
    if product.category_id:
        chosen = list(others.filter(category_id=product.category_id)[:RELATED_COUNT])
    if len(chosen) < RELATED_COUNT:
        taken = [p.pk for p in chosen]
        chosen += list(others.exclude(pk__in=taken)[: RELATED_COUNT - len(chosen)])
    return attach_prices(chosen, boutique)


# --- panier (session) ----------------------------------------------------------

def _all_carts(request):
    return request.session.setdefault(CART_SESSION_KEY, {})


def get_cart(request, slug):
    """{product_id (str): quantité (int)} du panier de cette boutique."""
    return dict(_all_carts(request).get(slug, {}))


def save_cart(request, slug, cart):
    carts = _all_carts(request)
    carts[slug] = cart
    request.session[CART_SESSION_KEY] = carts
    request.session.modified = True


def clear_cart(request, slug):
    save_cart(request, slug, {})


def cart_lines(request, boutique):
    """Lignes du panier relues depuis la base (prix/stock actuels) :
    [{product, quantity, unit_ttc, line_ttc, line_ht, in_stock}]. Les produits
    disparus de la vitrine sont ignorés."""
    cart = get_cart(request, boutique.shop_slug)
    if not cart:
        return []
    products = {str(p.id): p for p in published_products(boutique).filter(pk__in=list(cart))}
    attach_prices(products.values(), boutique)      # pose prix barré / promotion sur chaque produit
    overrides = price_overrides(boutique)
    lines = []
    for product_id, quantity in cart.items():
        product = products.get(product_id)
        if product is None:
            continue
        price_ht = unit_price_ht(product, overrides)
        line_ht, line_ttc = _line_totals(Decimal(quantity), price_ht, product.tva_rate)
        lines.append({
            "product": product, "quantity": quantity, "unit_price_ht": price_ht,
            "unit_ttc": unit_price_ttc(product, overrides), "line_ht": line_ht, "line_ttc": line_ttc,
            "in_stock": product.stock >= quantity,
        })
    return lines


def delivery_zones(boutique):
    return list(boutique.delivery_zones.all())


def cart_totals(boutique, lines, zone=None):
    """Sous-total, frais de livraison et total. Avec des zones de livraison,
    les frais dépendent de la ville choisie (`zone`) ; tant qu'elle ne l'est
    pas, `fee_pending` est vrai et le total est hors livraison. Sans zone,
    les frais uniques de la boutique s'appliquent."""
    subtotal = sum((line["line_ttc"] for line in lines), Decimal("0"))
    has_zones = boutique.delivery_zones.exists()
    if not lines:
        fee = Decimal("0")
    elif zone is not None:
        fee = zone.fee
    elif has_zones:
        fee = Decimal("0")
    else:
        fee = boutique.shop_delivery_fee
    return {"subtotal": subtotal, "delivery_fee": fee, "total": subtotal + fee,
            "count": sum(line["quantity"] for line in lines),
            "fee_pending": bool(lines) and has_zones and zone is None}
