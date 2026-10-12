"""Lecture du catalogue pour la vitrine publique : produits publiés, avec
stock et prix de la boutique, et panier en session."""

from decimal import Decimal

from django.db.models import DecimalField, OuterRef, Subquery, Value
from django.db.models.functions import Coalesce

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


def attach_prices(products, boutique):
    """Pose `shop_price` (TTC unitaire) sur chaque produit de la liste."""
    overrides = price_overrides(boutique)
    products = list(products)
    for product in products:
        product.shop_price = unit_price_ttc(product, overrides)
    return products


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


def cart_totals(boutique, lines):
    subtotal = sum((line["line_ttc"] for line in lines), Decimal("0"))
    fee = boutique.shop_delivery_fee if lines else Decimal("0")
    return {"subtotal": subtotal, "delivery_fee": fee, "total": subtotal + fee,
            "count": sum(line["quantity"] for line in lines)}
