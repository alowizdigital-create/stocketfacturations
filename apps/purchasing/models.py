from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BoutiqueScopedModel, CompteScopedModel, TimeStampedModel, UUIDModel


class Supplier(UUIDModel, CompteScopedModel, TimeStampedModel):
    """Fournisseur — partagé par toutes les boutiques de l'entreprise (comme
    le catalogue), contrairement aux clients qui sont propres à une boutique.
    Jamais supprimé : une réception passée doit toujours pouvoir dire de qui
    venait la marchandise. On le désactive (is_active) pour ne plus le
    proposer."""

    name = models.CharField(_("nom"), max_length=255)
    phone = models.CharField(_("téléphone"), max_length=30, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(_("adresse"), max_length=255, blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(_("actif"), default=True)

    class Meta:
        verbose_name = _("fournisseur")
        verbose_name_plural = _("fournisseurs")
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["compte", "name"], name="unique_supplier_name_par_compte"),
        ]

    def __str__(self):
        return self.name


class Purchase(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    """Réception de marchandises : ce qui est entré en stock dans une
    boutique, chez quel fournisseur et à quel coût. Enregistrée d'un bloc et
    non modifiable ensuite — c'est le registre d'où viennent les prix
    d'achat, on ne le réécrit pas après coup (une erreur se corrige par un
    ajustement de stock et un prix d'achat corrigé sur le produit)."""

    number = models.CharField(max_length=40)
    supplier = models.ForeignKey(
        Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name="purchases"
    )
    received_date = models.DateField(_("date de réception"), default=timezone.localdate)
    reference = models.CharField(
        _("référence fournisseur"), max_length=100, blank=True,
        help_text=_("N° de la facture ou du bon de livraison du fournisseur."),
    )
    note = models.TextField(blank=True)
    total_cost = models.DecimalField(max_digits=14, decimal_places=0, default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="purchases"
    )

    class Meta:
        verbose_name = _("réception de marchandises")
        verbose_name_plural = _("réceptions de marchandises")
        ordering = ["-received_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["boutique", "number"], name="unique_purchase_number_par_boutique"),
        ]

    def __str__(self):
        return self.number

    @staticmethod
    def generate_number(boutique, received_date=None):
        received_date = received_date or timezone.localdate()
        prefix = f"{boutique.code}-ACH-{received_date:%Y%m%d}-"
        existing = Purchase.objects.filter(boutique=boutique, number__startswith=prefix).count()
        return f"{prefix}{existing + 1:04d}"


class PurchaseLine(UUIDModel):
    purchase = models.ForeignKey(Purchase, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="purchase_lines")
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = models.DecimalField(_("coût unitaire"), max_digits=12, decimal_places=0)
    line_total = models.DecimalField(max_digits=14, decimal_places=0)
    # Prix d'achat du produit juste avant / juste après cette réception : montre
    # comment le prix moyen pondéré a bougé (None = produit sans prix d'achat
    # connu avant la réception).
    cost_before = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    cost_after = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
    # Mouvement de stock créé par cette ligne — le stock reste alimenté par le
    # registre habituel (StockMovement), la réception n'est qu'à son origine.
    movement = models.OneToOneField(
        "stock.StockMovement", on_delete=models.SET_NULL, null=True, blank=True, related_name="purchase_line"
    )
    position = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = _("ligne de réception")
        verbose_name_plural = _("lignes de réception")
        ordering = ["position"]

    def __str__(self):
        return f"{self.product} x{self.quantity}"


class Expense(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    """Dépense de fonctionnement d'une boutique (loyer, salaires, transport,
    électricité...) — tout ce qui réduit le bénéfice sans être du stock.
    Les marchandises achetées n'en font PAS partie : elles sont déjà dans le
    coût des produits vendus (voir Purchase et le rapport de marge), les
    compter ici aussi les décompterait deux fois.

    Jamais supprimée : une erreur s'annule (cancelled_at), ce qui la sort des
    totaux tout en gardant la trace de qui a saisi et qui a annulé. Si elle a
    été payée depuis une caisse individuelle, cette caisse est débitée à la
    saisie et recréditée à l'annulation (cash_movement)."""

    LOYER = "LOYER"
    SALAIRES = "SALAIRES"
    TRANSPORT = "TRANSPORT"
    ENERGIE = "ENERGIE"
    MARKETING = "MARKETING"
    ENTRETIEN = "ENTRETIEN"
    TAXES = "TAXES"
    AUTRE = "AUTRE"
    CATEGORY_CHOICES = [
        (LOYER, _("Loyer")),
        (SALAIRES, _("Salaires")),
        (TRANSPORT, _("Transport")),
        (ENERGIE, _("Électricité, eau, internet")),
        (MARKETING, _("Publicité, marketing")),
        (ENTRETIEN, _("Entretien, réparations")),
        (TAXES, _("Taxes et impôts")),
        (AUTRE, _("Autre")),
    ]

    category = models.CharField(_("catégorie"), max_length=20, choices=CATEGORY_CHOICES)
    label = models.CharField(_("libellé"), max_length=255)
    amount = models.DecimalField(_("montant"), max_digits=14, decimal_places=0)
    expense_date = models.DateField(_("date"), default=timezone.localdate)
    note = models.TextField(blank=True)
    cash_movement = models.OneToOneField(
        "cashier.PersonalCashMovement", on_delete=models.SET_NULL, null=True, blank=True, related_name="expense"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="expenses"
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = _("dépense")
        verbose_name_plural = _("dépenses")
        ordering = ["-expense_date", "-created_at"]

    def __str__(self):
        return f"{self.label} ({self.amount})"

    @property
    def is_cancelled(self):
        return self.cancelled_at is not None
