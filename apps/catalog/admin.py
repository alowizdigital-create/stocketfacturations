from django.contrib import admin

from .models import Category, Product, ProductBoutiquePrice, ProductImage, Unit


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "compte", "parent"]
    list_filter = ["compte"]


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ["name", "symbol", "compte"]
    list_filter = ["compte"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["name", "compte", "category", "default_sale_price", "tva_rate", "is_active"]
    list_filter = ["compte", "category", "is_active"]
    search_fields = ["name", "sku", "barcode"]


@admin.register(ProductBoutiquePrice)
class ProductBoutiquePriceAdmin(admin.ModelAdmin):
    list_display = ["product", "boutique", "price_override", "is_available"]
    list_filter = ["boutique"]


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ["product", "position"]
