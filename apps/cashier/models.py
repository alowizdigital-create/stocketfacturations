from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.models import BoutiqueScopedModel, TimeStampedModel, UUIDModel


class CashSession(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    """Une session de caisse par boutique à la fois (voir la contrainte
    ci-dessous) — pas une session par caissier/prise de poste, partagée
    par tout le personnel de la boutique jusqu'à la clôture. "Un jour"
    n'a pas de sens calendaire ici : une session ouverte le soir et
    fermée après minuit reste la même session, aucune coupure automatique."""

    OUVERTE = "OUVERTE"
    FERMEE = "FERMEE"
    STATUS_CHOICES = [
        (OUVERTE, _("Ouverte")),
        (FERMEE, _("Fermée")),
    ]

    number = models.CharField(max_length=40)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=OUVERTE)
    opening_amount = models.DecimalField(max_digits=14, decimal_places=0)
    opened_at = models.DateTimeField(default=timezone.now)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="cash_sessions_opened"
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="cash_sessions_closed"
    )
    counted_amount = models.DecimalField(max_digits=14, decimal_places=0, null=True, blank=True)
    closing_note = models.TextField(blank=True)

    class Meta:
        verbose_name = _("session de caisse")
        verbose_name_plural = _("sessions de caisse")
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["boutique"], condition=Q(status="OUVERTE"), name="unique_open_session_par_boutique"
            ),
        ]

    def __str__(self):
        return self.number

    @staticmethod
    def generate_number(boutique, opened_at=None):
        """Même principe que Sale.generate_number/Invoice.generate_commande_number
        (préfixe boutique + tag + date + séquence du jour, 100% local)."""
        opened_at = opened_at or timezone.localdate()
        if hasattr(opened_at, "date"):
            opened_at = opened_at.date()
        prefix = f"{boutique.code}-CAISSE-{opened_at:%Y%m%d}-"
        existing = CashSession.objects.filter(boutique=boutique, number__startswith=prefix).count()
        return f"{prefix}{existing + 1:04d}"

    def expected_amount(self):
        """Fond de départ + ventes en espèces de la période + entrées
        manuelles - sorties manuelles. Toujours recalculé, jamais mis en
        cache (comme Invoice.balance_due) — pas d'invalidation à gérer.

        Les ventes en espèces ne sont PAS un CashMovement synchronisé :
        elles sont dérivées à la volée des Payment déjà synchronisés via
        le protocole existant (voir la note de conception dans le plan).
        Seule la devise de la boutique compte — un tiroir de caisse ne
        contient physiquement qu'une seule devise."""
        from apps.sales.models import Payment

        cash_sales = Payment.objects.filter(
            boutique=self.boutique,
            method=Payment.ESPECES,
            invoice__currency=self.boutique.devise,
            paid_at__gte=self.opened_at,
            paid_at__lte=self.closed_at or timezone.now(),
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

        movements = self.movements.aggregate(
            entrees=Sum("amount", filter=Q(type=CashMovement.ENTREE)),
            sorties=Sum("amount", filter=Q(type=CashMovement.SORTIE)),
        )
        entrees = movements["entrees"] or Decimal("0")
        sorties = movements["sorties"] or Decimal("0")
        return self.opening_amount + cash_sales + entrees - sorties

    @property
    def discrepancy(self):
        if self.counted_amount is None:
            return None
        return self.counted_amount - self.expected_amount()


class CashMovement(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    """Mouvement manuel d'argent (hors-vente) sur une session de caisse —
    ex: sortie pour payer une course, appoint ajouté en cours de journée.
    Les ventes en espèces n'ont pas de CashMovement : voir
    CashSession.expected_amount()."""

    ENTREE = "ENTREE"
    SORTIE = "SORTIE"
    TYPE_CHOICES = [
        (ENTREE, _("Entrée")),
        (SORTIE, _("Sortie")),
    ]

    SOURCE_ONLINE = "ONLINE"
    SOURCE_OFFLINE = "OFFLINE"
    SOURCE_CHOICES = [
        (SOURCE_ONLINE, _("En ligne")),
        (SOURCE_OFFLINE, _("Hors-ligne")),
    ]

    session = models.ForeignKey(CashSession, on_delete=models.CASCADE, related_name="movements")
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    reason = models.CharField(_("motif"), max_length=255)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="cash_movements"
    )
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_ONLINE)

    class Meta:
        verbose_name = _("mouvement de caisse")
        verbose_name_plural = _("mouvements de caisse")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_type_display()} {self.amount} — {self.reason}"


class PersonalCashMovement(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    """Mouvement sur la caisse individuelle d'un employé — distincte de la
    session de caisse partagée par boutique ci-dessus (CashSession) : ici
    chacun a son propre solde. Le solde n'est jamais stocké, toujours
    recalculé (voir services.personal_cash_balance) : crédits = ventes en
    espèces encaissées personnellement (dérivées de Payment.created_by,
    même principe que CashSession.expected_amount()) + transferts reçus ;
    débits = transferts envoyés + frais de livraison. Un transfert crée
    deux lignes liées (DEBIT chez l'un, CREDIT chez l'autre, même
    transfer_id) plutôt qu'un modèle de transfert séparé, pour que le
    solde de chacun reste une simple somme sur ce seul modèle."""

    CREDIT = "CREDIT"
    DEBIT = "DEBIT"
    TYPE_CHOICES = [(CREDIT, _("Crédit")), (DEBIT, _("Débit"))]

    TRANSFERT = "TRANSFERT"
    LIVRAISON = "LIVRAISON"
    KIND_CHOICES = [
        (TRANSFERT, _("Transfert entre collaborateurs")),
        (LIVRAISON, _("Frais de livraison")),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="personal_cash_movements"
    )
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    reason = models.CharField(_("motif"), max_length=255, blank=True)
    # Autre partie d'un transfert (qui a reçu/envoyé) — non renseigné pour
    # un débit "frais de livraison".
    counterparty = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # Lie les deux lignes (DEBIT + CREDIT) d'un même transfert — permet de
    # les retrouver/afficher ensemble sans dépendre de l'ordre de création.
    transfer_id = models.UUIDField(null=True, blank=True)
    commande = models.ForeignKey(
        "sales.Invoice", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
        help_text=_("Commande à l'origine du débit, pour un mouvement de type « Frais de livraison »."),
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="personal_cash_movements_created"
    )

    class Meta:
        verbose_name = _("mouvement de caisse individuelle")
        verbose_name_plural = _("mouvements de caisse individuelle")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_type_display()} {self.amount} — {self.user} ({self.get_kind_display()})"


class CashTransfer(UUIDModel, BoutiqueScopedModel, TimeStampedModel):
    """Demande de transfert entre deux caisses individuelles — tant qu'elle
    n'est pas acceptée par le destinataire, aucun PersonalCashMovement
    n'existe encore : l'argent reste dans la caisse de l'expéditeur (voir
    services.request_transfer/accept_transfer/reject_transfer). Les deux
    PersonalCashMovement (DEBIT + CREDIT) ne sont créés qu'à l'acceptation."""

    EN_ATTENTE = "EN_ATTENTE"
    ACCEPTE = "ACCEPTE"
    REFUSE = "REFUSE"
    STATUS_CHOICES = [
        (EN_ATTENTE, _("En attente")),
        (ACCEPTE, _("Accepté")),
        (REFUSE, _("Refusé")),
    ]

    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cash_transfers_sent"
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cash_transfers_received"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=0)
    reason = models.CharField(_("motif"), max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=EN_ATTENTE)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="cash_transfers_created"
    )

    class Meta:
        verbose_name = _("transfert de caisse")
        verbose_name_plural = _("transferts de caisse")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.from_user} → {self.to_user} : {self.amount} ({self.get_status_display()})"
