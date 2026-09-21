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
    # Coût d'achat unitaire du produit AU MOMENT de la vente (photo, pas un
    # lien vivant) : la marge d'une vente passée ne doit pas changer quand le
    # prix d'achat évolue ensuite. None = coût alors inconnu (produit sans prix
    # d'achat, ligne libre, ou vente antérieure à cette fonctionnalité) : ces
    # lignes sont exclues du calcul de marge plutôt que comptées à coût zéro.
    unit_cost = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True)
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

    # Suivi de livraison — ne concerne que les commandes (type=COMMANDE) :
    # indépendant du statut paiement/facturation ci-dessus, une commande
    # peut être en attente/en préparation/livrée quel que soit son statut
    # de règlement. Champ porté par Invoice (comme note/pdf_format) plutôt
    # qu'un modèle séparé, pour rester simple. Trois niveaux : EN_ATTENTE
    # (venant d'être passée, pas encore commencée) -> EN_COURS (en cours
    # de préparation) -> LIVREE (remise au client).
    EN_ATTENTE = "EN_ATTENTE"
    EN_COURS = "EN_COURS"
    LIVREE = "LIVREE"
    DELIVERY_STATUS_CHOICES = [
        (EN_ATTENTE, _("En attente")),
        (EN_COURS, _("En cours")),
        (LIVREE, _("Livrée")),
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
    # Numéro simple d'une commande (1, 2, 3...), propre à la boutique : celui
    # qu'on annonce au client et qu'on lit dans la liste des commandes, à la
    # place du numéro de document long (`number`). Attribué à la création
    # (voir Invoice.next_commande_seq) ; vide pour les devis et les factures.
    commande_seq = models.PositiveIntegerField(_("n° de commande"), null=True, blank=True)
    delivery_status = models.CharField(
        _("statut de livraison"), max_length=15, choices=DELIVERY_STATUS_CHOICES, default=EN_ATTENTE,
    )
    delivered_at = models.DateTimeField(_("livrée le"), null=True, blank=True)
    delivered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="commandes_livrees",
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

    @staticmethod
    def next_commande_seq(boutique):
        """Prochain numéro simple de commande de la boutique : le plus grand
        déjà attribué + 1 (les commandes annulées gardent le leur : un numéro
        annoncé à un client n'est jamais réutilisé)."""
        last = Invoice.objects.filter(boutique=boutique, type=Invoice.COMMANDE).aggregate(m=models.Max("commande_seq"))["m"]
        return (last or 0) + 1

    @property
    def amount_paid(self):
        return self.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    @property
    def balance_due(self):
        return max(self.total_ttc - self.amount_paid, Decimal("0"))

    @property
    def payment_history(self):
        """Versements dans l'ordre chronologique (le plus ancien d'abord),
        chacun avec le cumul payé et le reste à payer APRÈS lui. `settles`
        marque le versement qui ramène le reste à zéro — le « solde » de la
        facture. Source unique pour la fiche facture, le PDF et la page
        publique, pour que les trois montrent exactement les mêmes dates.
        Trié en Python (pas order_by) pour réutiliser un prefetch éventuel
        de `payments`."""
        history = []
        paid = Decimal("0")
        settled = False
        for payment in sorted(self.payments.all(), key=lambda p: (p.paid_at, p.created_at)):
            paid += payment.amount
            remaining = max(self.total_ttc - paid, Decimal("0"))
            settles = remaining == 0 and not settled
            settled = settled or settles
            history.append(
                {"payment": payment, "paid_total": paid, "remaining": remaining, "settles": settles}
            )
        return history

    @property
    def settled_at(self):
        """Date/heure du versement qui a soldé la facture, None tant qu'il
        reste un montant à payer."""
        return next((h["payment"].paid_at for h in self.payment_history if h["settles"]), None)


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


class PaymentReminder(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    """Trace d'une relance de paiement envoyée (ou préparée) à un client :
    sert à afficher « dernière relance le… » et à éviter de harceler le même
    client plusieurs fois par jour. Journal simple, jamais modifié.
    `invoice` vide = relance de relevé (toutes les factures impayées du
    client d'un coup). `amount` = ce que le message réclamait à ce moment-là."""

    API = "API"
    LINK = "LINK"
    CHANNEL_CHOICES = [(API, _("Envoyée par l'API WhatsApp")), (LINK, _("Ouverte dans WhatsApp"))]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="reminders")
    invoice = models.ForeignKey(
        "Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="reminders"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    currency = models.CharField(max_length=3, default="XOF")
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="payment_reminders"
    )

    class Meta:
        verbose_name = _("relance de paiement")
        verbose_name_plural = _("relances de paiement")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.client} — {self.amount} {self.currency}"
