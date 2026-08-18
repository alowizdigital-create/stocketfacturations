from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BoutiqueScopedModel, CompteScopedModel, TimeStampedModel, UUIDModel


class Client(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    nif = models.CharField(_("NIF / identifiant fiscal"), max_length=50, blank=True)

    class Meta:
        verbose_name = _("client")
        verbose_name_plural = _("clients")

    def __str__(self):
        return self.name


class TaxRate(UUIDModel, CompteScopedModel, TimeStampedModel):
    name = models.CharField(max_length=100)
    rate = models.DecimalField(_("taux (%)"), max_digits=5, decimal_places=2)
    is_default = models.BooleanField(default=False)
    active_from = models.DateField(default=timezone.localdate)

    class Meta:
        verbose_name = _("taux de TVA")
        verbose_name_plural = _("taux de TVA")

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
        (BROUILLON, _("Brouillon")),
        (CONFIRMEE, _("Confirmée")),
        (ANNULEE, _("Annulée")),
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
        verbose_name = _("vente")
        verbose_name_plural = _("ventes")
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
        verbose_name = _("ligne de vente")
        verbose_name_plural = _("lignes de vente")
        ordering = ["position"]

    def __str__(self):
        return f"{self.description} x{self.quantity}"


class Invoice(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    DEVIS = "DEVIS"
    FACTURE = "FACTURE"
    COMMANDE = "COMMANDE"
    TYPE_CHOICES = [(DEVIS, _("Devis")), (FACTURE, _("Facture")), (COMMANDE, _("Commande"))]

    BROUILLON = "BROUILLON"
    VALIDEE = "VALIDEE"
    PAYEE = "PAYEE"
    PARTIELLEMENT_PAYEE = "PARTIELLEMENT_PAYEE"
    ANNULEE = "ANNULEE"
    CONVERTIE = "CONVERTIE"
    STATUS_CHOICES = [
        (BROUILLON, _("Brouillon")),
        (VALIDEE, _("Validée")),
        (PAYEE, _("Payée")),
        (PARTIELLEMENT_PAYEE, _("Partiellement payée")),
        (ANNULEE, _("Annulée")),
        (CONVERTIE, _("Convertie en facture")),
    ]

    PDF_FORMAT_80MM = "80MM"
    PDF_FORMAT_A4 = "A4"
    PDF_FORMAT_CHOICES = [(PDF_FORMAT_80MM, _("Ticket 80mm")), (PDF_FORMAT_A4, "A4")]

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
        _("remise globale (FCFA)"), max_digits=14, decimal_places=0, default=0
    )
    note = models.TextField(
        _("note interne"), blank=True,
        help_text=_("Usage interne uniquement — n'apparaît jamais sur le reçu/PDF."),
    )
    pdf_format = models.CharField(
        _("format du PDF"), max_length=10, choices=PDF_FORMAT_CHOICES, default=PDF_FORMAT_80MM,
    )
    converted_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="conversions"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="invoices"
    )

    class Meta:
        verbose_name = _("facture / devis")
        verbose_name_plural = _("factures / devis")
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

    @staticmethod
    def generate_commande_number(boutique, issue_date=None):
        """Préfixe `-CMD-` distinct (même principe que Sale.generate_number
        avec `-V-`) : une commande se repère au premier coup d'œil et ne
        partage jamais son compteur avec les devis/factures."""
        issue_date = issue_date or timezone.localdate()
        prefix = f"{boutique.code}-CMD-{issue_date:%Y%m%d}-"
        existing = Invoice.objects.filter(boutique=boutique, number__startswith=prefix).count()
        return f"{prefix}{existing + 1:04d}"

    @property
    def amount_paid(self):
        return self.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    @property
    def balance_due(self):
        return max(self.total_ttc - self.amount_paid, Decimal("0"))


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
        _("remise (FCFA)"), max_digits=12, decimal_places=0, default=0
    )
    line_total_ht = models.DecimalField(max_digits=14, decimal_places=0)
    line_total_ttc = models.DecimalField(max_digits=14, decimal_places=0)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("ligne de facture")
        verbose_name_plural = _("lignes de facture")
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
        (ESPECES, _("Espèces")),
        (MOBILE_MONEY, "Mobile Money"),
        (VIREMENT, _("Virement")),
        (CARTE, _("Carte")),
        (CHEQUE, _("Chèque")),
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
        verbose_name = _("paiement")
        verbose_name_plural = _("paiements")
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.amount} {self.invoice.currency} ({self.get_method_display()})"
