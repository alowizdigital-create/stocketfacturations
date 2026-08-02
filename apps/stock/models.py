from django.conf import settings
from django.db import models

from apps.catalog.models import Product
from apps.core.models import BoutiqueScopedModel, TimeStampedModel, UUIDModel


class StockMovement(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    """Registre append-only : la seule source de vérité du stock. Jamais
    modifié après création — la synchronisation offline/online devient un
    simple "insert si absent", donc structurellement sans conflit."""

    ENTREE = "ENTREE"
    SORTIE = "SORTIE"
    AJUSTEMENT = "AJUSTEMENT"
    VENTE = "VENTE"
    RETOUR = "RETOUR"
    TYPE_CHOICES = [
        (ENTREE, "Entrée"),
        (SORTIE, "Sortie"),
        (AJUSTEMENT, "Ajustement"),
        (VENTE, "Vente"),
        (RETOUR, "Retour"),
    ]

    SOURCE_ONLINE = "ONLINE"
    SOURCE_OFFLINE = "OFFLINE"
    SOURCE_CHOICES = [
        (SOURCE_ONLINE, "En ligne"),
        (SOURCE_OFFLINE, "Hors-ligne"),
    ]

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="movements")
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    # Signée : positive = entrée, négative = sortie.
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    reference_invoice = models.ForeignKey(
        "sales.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements"
    )
    reference_sale = models.ForeignKey(
        "sales.Sale", on_delete=models.SET_NULL, null=True, blank=True, related_name="stock_movements"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="stock_movements"
    )
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_ONLINE)

    class Meta:
        verbose_name = "mouvement de stock"
        verbose_name_plural = "mouvements de stock"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_type_display()} {self.quantity} x {self.product} ({self.boutique})"


class StockLevel(models.Model):
    """Cache dérivé du registre, jamais édité directement — recalculé par
    apply_movement(). Ne jamais l'utiliser comme source de vérité pour un
    correctif manuel : passer par un StockMovement d'AJUSTEMENT."""

    boutique = models.ForeignKey("tenants.Boutique", on_delete=models.CASCADE, related_name="stock_levels")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="stock_levels")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "niveau de stock"
        verbose_name_plural = "niveaux de stock"
        constraints = [
            models.UniqueConstraint(fields=["boutique", "product"], name="unique_stock_level_boutique_product"),
        ]

    def __str__(self):
        return f"{self.product} @ {self.boutique}: {self.quantity}"
