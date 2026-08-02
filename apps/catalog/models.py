from django.db import models

from apps.core.models import CompteScopedModel, TimeStampedModel, UUIDModel
from apps.tenants.models import Boutique


class Category(UUIDModel, CompteScopedModel, TimeStampedModel):
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children"
    )

    class Meta:
        verbose_name = "catégorie"
        verbose_name_plural = "catégories"

    def __str__(self):
        return self.name


class Unit(UUIDModel, CompteScopedModel, TimeStampedModel):
    """Unité de mesure définie par l'entreprise (Pièce, Kg, Litre, Carton,
    Sac...) — plus de liste figée en dur, chaque Compte gère les siennes."""

    name = models.CharField("nom", max_length=50)
    symbol = models.CharField("symbole", max_length=10, blank=True)

    class Meta:
        verbose_name = "unité"
        verbose_name_plural = "unités"
        constraints = [
            models.UniqueConstraint(fields=["compte", "name"], name="unique_unit_name_par_compte"),
        ]

    def __str__(self):
        return f"{self.name} ({self.symbol})" if self.symbol else self.name


class Product(UUIDModel, CompteScopedModel, TimeStampedModel):
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="products"
    )
    name = models.CharField(max_length=255)
    sku = models.CharField("référence (SKU)", max_length=64, blank=True)
    barcode = models.CharField("code-barres", max_length=64, blank=True)
    image = models.ImageField("photo", upload_to="produits/", null=True, blank=True)
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="products")
    default_sale_price = models.DecimalField(
        "prix de vente (FCFA)", max_digits=12, decimal_places=0
    )
    tva_rate = models.DecimalField(
        "taux de TVA (%)", max_digits=5, decimal_places=2, default=0
    )
    low_stock_threshold_default = models.PositiveIntegerField("seuil de stock bas", default=5)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "produit"
        verbose_name_plural = "produits"
        constraints = [
            models.UniqueConstraint(fields=["compte", "sku"], name="unique_sku_par_compte", condition=~models.Q(sku="")),
        ]

    def __str__(self):
        return self.name


class ProductBoutiquePrice(models.Model):
    """Surcharge optionnelle de prix/disponibilité pour une boutique
    donnée, sans dupliquer le produit (catalogue partagé au niveau du
    Compte)."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="boutique_overrides")
    boutique = models.ForeignKey(Boutique, on_delete=models.CASCADE, related_name="product_overrides")
    price_override = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    low_stock_threshold_override = models.PositiveIntegerField(null=True, blank=True)
    is_available = models.BooleanField(default=True)

    class Meta:
        verbose_name = "prix boutique"
        verbose_name_plural = "prix boutique"
        constraints = [
            models.UniqueConstraint(fields=["product", "boutique"], name="unique_product_boutique_price"),
        ]

    def __str__(self):
        return f"{self.product} @ {self.boutique}"
