from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import BoutiqueScopedModel, CompteScopedModel, TimeStampedModel, UUIDModel

 
class Client(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    nif = models.CharField("NIF / identifiant fiscal", max_length=50, blank=True)

    class Meta:
        verbose_name = "client"
        verbose_name_plural = "clients"

    def __str__(self):
        return self.name


class TaxRate(UUIDModel, CompteScopedModel, TimeStampedModel):
    name = models.CharField(max_length=100)
    rate = models.DecimalField("taux (%)", max_digits=5, decimal_places=2)
    is_default = models.BooleanField(default=False)
    active_from = models.DateField(default=timezone.localdate)

    class Meta:
        verbose_name = "taux de TVA"
        verbose_name_plural = "taux de TVA"

    def __str__(self):
        return f"{self.name} ({self.rate}%)"


class Sale(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    """La vente elle-même : c'est cet enregistrement qui déduit le stock à
    la confirmation. La facture (Invoice) n'est qu'un document généré
    ensuite, à la demande, à partir d'une vente confirmée — voir
    services.generate_invoice_from_sale()."""

    BROUILLON = "BROUILLON"
    CONFIRMEE = "CONFIRMEE"
    ANNULEE = "ANNULEE"
    STATUS_CHOICES = [
        (BROUILLON, "Brouillon"),
        (CONFIRMEE, "Confirmée"),
        (ANNULEE, "Annulée"),
    ]

    client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True, related_name="sales"
    )
    number = models.CharField(max_length=40)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=BROUILLON)
    sale_date = models.DateField(default=timezone.localdate)
    currency = models.CharField(max_length=3, default="XOF")
    subtotal_ht = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    total_tva = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    total_ttc = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    invoice = models.OneToOneField(
        "Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="sale"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="sales"
    )

    class Meta:
        verbose_name = "vente"
        verbose_name_plural = "ventes"
        ordering = ["-sale_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["boutique", "number"], name="unique_sale_number_par_boutique"),
        ]

    def __str__(self):
        return self.number

    @staticmethod
    def generate_number(boutique, sale_date=None):
        sale_date = sale_date or timezone.localdate()
        prefix = f"{boutique.code}-V-{sale_date:%Y%m%d}-"
        existing = Sale.objects.filter(boutique=boutique, number__startswith=prefix).count()
        return f"{prefix}{existing + 1:04d}"


class SaleLine(UUIDModel):
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="sale_lines"
    )
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price_ht = models.DecimalField(max_digits=12, decimal_places=0)
    tva_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    line_total_ht = models.DecimalField(max_digits=14, decimal_places=0)
    line_total_ttc = models.DecimalField(max_digits=14, decimal_places=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "ligne de vente"
        verbose_name_plural = "lignes de vente"
        ordering = ["position"]

    def __str__(self):
        return f"{self.description} x{self.quantity}"


class Invoice(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    DEVIS = "DEVIS"
    FACTURE = "FACTURE"
    TYPE_CHOICES = [(DEVIS, "Devis"), (FACTURE, "Facture")]

    BROUILLON = "BROUILLON"
    VALIDEE = "VALIDEE"
    PAYEE = "PAYEE"
    PARTIELLEMENT_PAYEE = "PARTIELLEMENT_PAYEE"
    ANNULEE = "ANNULEE"
    CONVERTIE = "CONVERTIE"
    STATUS_CHOICES = [
        (BROUILLON, "Brouillon"),
        (VALIDEE, "Validée"),
        (PAYEE, "Payée"),
        (PARTIELLEMENT_PAYEE, "Partiellement payée"),
        (ANNULEE, "Annulée"),
        (CONVERTIE, "Convertie en facture"),
    ]

    client = models.ForeignKey(
        Client, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices"
    )
    number = models.CharField(max_length=40)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=FACTURE)
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default=BROUILLON)
    issue_date = models.DateField(default=timezone.localdate)
    due_date = models.DateField(null=True, blank=True)
    currency = models.CharField(max_length=3, default="XOF")
    subtotal_ht = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    total_tva = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    total_ttc = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    discount_amount = models.DecimalField(
        "remise globale (FCFA)", max_digits=14, decimal_places=0, default=0
    )
    converted_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="conversions"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="invoices"
    )

    class Meta:
        verbose_name = "facture / devis"
        verbose_name_plural = "factures / devis"
        ordering = ["-issue_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["boutique", "number"], name="unique_invoice_number_par_boutique"),
        ]

    def __str__(self):
        return f"{self.number} ({self.get_type_display()})"

    @staticmethod
    def generate_number(boutique, issue_date=None):
        """Numérotation générée localement (préfixe boutique + date +
        séquence du jour) — fonctionne 100% hors-ligne, sans coordination
        avec le serveur central."""
        issue_date = issue_date or timezone.localdate()
        prefix = f"{boutique.code}-{issue_date:%Y%m%d}-"
        existing = Invoice.objects.filter(boutique=boutique, number__startswith=prefix).count()
        return f"{prefix}{existing + 1:04d}"


class InvoiceLine(UUIDModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_lines"
    )
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_price_ht = models.DecimalField(max_digits=12, decimal_places=0)
    tva_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_amount = models.DecimalField(
        "remise (FCFA)", max_digits=12, decimal_places=0, default=0
    )
    line_total_ht = models.DecimalField(max_digits=14, decimal_places=0)
    line_total_ttc = models.DecimalField(max_digits=14, decimal_places=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "ligne de facture"
        verbose_name_plural = "lignes de facture"
        ordering = ["position"]

    def __str__(self):
        return f"{self.description} x{self.quantity}"


class Payment(UUIDModel, TimeStampedModel):
    ESPECES = "ESPECES"
    MOBILE_MONEY = "MOBILE_MONEY"
    VIREMENT = "VIREMENT"
    CARTE = "CARTE"
    CHEQUE = "CHEQUE"
    METHOD_CHOICES = [
        (ESPECES, "Espèces"),
        (MOBILE_MONEY, "Mobile Money"),
        (VIREMENT, "Virement"),
        (CARTE, "Carte"),
        (CHEQUE, "Chèque"),
    ]

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    boutique = models.ForeignKey("tenants.Boutique", on_delete=models.CASCADE, related_name="+")
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    method = models.CharField(max_length=20, choices=METHOD_CHOICES, default=ESPECES)
    reference = models.CharField(max_length=100, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="payments"
    )

    class Meta:
        verbose_name = "paiement"
        verbose_name_plural = "paiements"
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.amount} {self.invoice.currency} ({self.get_method_display()})"
