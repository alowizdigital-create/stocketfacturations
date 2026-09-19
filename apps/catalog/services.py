from django.utils import timezone

from .models import Product, ProductBoutiquePrice


def expired_products_in_stock(compte, boutique):
    """Produits périmés dont la boutique a encore du stock — un produit
    périmé sans stock dans cette boutique n'est pas un problème à signaler
    ici. Source unique pour la carte du tableau de bord ET la liste
    filtrée (catalog:product_list?expired=1), pour que le nombre affiché
    corresponde toujours à ce qu'on voit en cliquant dessus."""
    return Product.objects.filter(
        compte=compte,
        expiry_date__lt=timezone.localdate(),
        stock_levels__boutique=boutique,
        stock_levels__quantity__gt=0,
    ).distinct()


def get_effective_price(product, boutique):
    override = ProductBoutiquePrice.objects.filter(product=product, boutique=boutique).first()
    if override and override.price_override is not None:
        return override.price_override
    return product.default_sale_price


def get_effective_low_stock_threshold(product, boutique):
    override = ProductBoutiquePrice.objects.filter(product=product, boutique=boutique).first()
    if override and override.low_stock_threshold_override is not None:
        return override.low_stock_threshold_override
    return product.low_stock_threshold_default
