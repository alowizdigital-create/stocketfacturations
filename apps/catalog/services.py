from .models import ProductBoutiquePrice


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
