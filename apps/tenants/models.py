import hashlib
import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel, UUIDModel


class Compte(UUIDModel, TimeStampedModel):
    """Une entreprise cliente indépendante de la plateforme (SaaS
    multi-entreprises) : toutes les données d'un Compte sont strictement
    isolées de celles des autres Comptes."""

    name = models.CharField("nom de l'entreprise", max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    logo = models.ImageField(
        "logo de l'entreprise", upload_to="logos/", null=True, blank=True,
        help_text="Affiché en haut des factures/devis (ticket 80 mm).",
    )

    class Meta:
        verbose_name = "entreprise"
        verbose_name_plural = "entreprises"

    def __str__(self):
        return self.name


class Boutique(UUIDModel, TimeStampedModel):
    compte = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name="boutiques")
    name = models.CharField("nom de la boutique", max_length=255)
    code = models.CharField(
        "code court",
        max_length=10,
        help_text="Préfixe utilisé pour numéroter les factures hors-ligne (ex: BTQ-001).",
    )
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    devise = models.CharField(max_length=3, default="XOF")
    country_calling_code = models.CharField(
        "indicatif téléphonique du pays",
        max_length=4,
        blank=True,
        help_text=(
            "Complète automatiquement les numéros WhatsApp saisis sans "
            "indicatif (ex: 07... devient +225 07...)."
        ),
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        "boutique par défaut",
        default=False,
        help_text="Boutique sélectionnée automatiquement à la connexion pour cette entreprise.",
    )

    # Moyens de paiement Mobile Money — affichés sur les factures/devis
    # (PDF et page publique) pour que le client sache où payer. Vides par
    # défaut : rien ne s'affiche tant que la boutique ne les a pas remplis.
    om_account_name = models.CharField("Orange Money — nom du compte", max_length=100, blank=True)
    om_number = models.CharField("Orange Money — numéro", max_length=30, blank=True)
    momo_account_name = models.CharField("MTN MoMo — nom du compte", max_length=100, blank=True)
    momo_number = models.CharField("MTN MoMo — numéro", max_length=30, blank=True)

    class Meta:
        verbose_name = "boutique"
        verbose_name_plural = "boutiques"
        constraints = [
            models.UniqueConstraint(fields=["compte", "code"], name="unique_boutique_code_par_compte"),
        ]

    def __str__(self):
        return f"{self.name} ({self.compte.name})"

    def save(self, *args, **kwargs):
        if self.is_default:
            Boutique.objects.filter(compte=self.compte).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    @property
    def payment_methods(self):
        """Moyens de paiement Mobile Money configurés, prêts à afficher sur
        une facture/devis — liste vide si rien n'est renseigné."""
        methods = []
        if self.om_number:
            methods.append({"label": "OM", "account_name": self.om_account_name, "number": self.om_number})
        if self.momo_number:
            methods.append({"label": "MoMo", "account_name": self.momo_account_name, "number": self.momo_number})
        return methods

    @property
    def exchange_rate_map(self):
        """{code devise: taux}, taux exprimé en devise de la boutique pour
        1 unité de cette devise (la devise de la boutique elle-même vaut
        toujours 1). Utilisé pour convertir les montants d'une vente/d'un
        devis quand une devise différente est choisie — voir
        apps.sales.forms / sale_form.html / invoice_form.html."""
        rates = {self.devise: Decimal("1")}
        for rate in self.exchange_rates.all():
            rates[rate.currency] = rate.rate
        return rates


class ExchangeRate(UUIDModel, TimeStampedModel):
    """Taux de change d'une devise étrangère par rapport à la devise par
    défaut de la boutique (ex: 1 EUR = 655,957 XOF) — permet de proposer
    une vente/un devis dans une autre devise avec conversion automatique
    des montants."""

    boutique = models.ForeignKey(Boutique, on_delete=models.CASCADE, related_name="exchange_rates")
    currency = models.CharField("devise", max_length=3)
    rate = models.DecimalField(
        "taux",
        max_digits=14,
        decimal_places=4,
        help_text="Valeur de 1 unité de cette devise, exprimée dans la devise de la boutique. Ex: 1 EUR = 655.9570 XOF.",
    )

    class Meta:
        verbose_name = "taux de change"
        verbose_name_plural = "taux de change"
        ordering = ["currency"]
        constraints = [
            models.UniqueConstraint(fields=["boutique", "currency"], name="unique_exchange_rate_par_devise"),
        ]

    def __str__(self):
        return f"1 {self.currency} = {self.rate} {self.boutique.devise}"


class Membership(UUIDModel, TimeStampedModel):
    ADMIN_COMPTE = "ADMIN_COMPTE"
    GERANT_BOUTIQUE = "GERANT_BOUTIQUE"
    CAISSIER = "CAISSIER"
    ROLE_CHOICES = [
        (ADMIN_COMPTE, "Administrateur de l'entreprise"),
        (GERANT_BOUTIQUE, "Gérant de boutique"),
        (CAISSIER, "Caissier"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    boutique = models.ForeignKey(Boutique, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "affectation"
        verbose_name_plural = "affectations"
        constraints = [
            models.UniqueConstraint(fields=["user", "boutique"], name="unique_membership_user_boutique"),
        ]

    def __str__(self):
        return f"{self.user} @ {self.boutique} ({self.get_role_display()})"


def _generate_token():
    return secrets.token_urlsafe(40)


def _hash_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class BoutiqueAPIToken(models.Model):
    """Credential unique d'un poste offline pour dialoguer avec l'API de
    synchronisation. La boutique n'est jamais déduite du corps de la
    requête, toujours de ce token."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    boutique = models.OneToOneField(Boutique, on_delete=models.CASCADE, related_name="api_token")
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    # auto_now (pas auto_now_add) : la régénération d'un jeton passe par
    # update_or_create() sur la même ligne (un seul jeton par boutique,
    # voir issue()) — auto_now_add ne se déclenche qu'à la toute première
    # création et resterait figé sur la date du tout premier jeton généré,
    # jamais mis à jour lors d'une régénération ultérieure.
    created_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    last_push_at = models.DateTimeField(null=True, blank=True)
    last_pull_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "jeton API boutique"
        verbose_name_plural = "jetons API boutique"

    @classmethod
    def issue(cls, boutique):
        """Crée (ou remplace) le jeton d'une boutique et retourne le
        jeton en clair — seule occasion où il est lisible."""
        raw_token = _generate_token()
        obj, _ = cls.objects.update_or_create(
            boutique=boutique,
            defaults={"token_hash": _hash_token(raw_token), "is_active": True},
        )
        return obj, raw_token

    @classmethod
    def resolve(cls, raw_token):
        try:
            return cls.objects.select_related("boutique__compte").get(
                token_hash=_hash_token(raw_token), is_active=True
            )
        except cls.DoesNotExist:
            return None

    def __str__(self):
        return f"Jeton de {self.boutique}"


class Plan(UUIDModel, TimeStampedModel):
    """Catalogue des offres d'abonnement — communes à toute la plateforme,
    pas propres à un Compte. Gérées via l'admin Django (pas de back-office
    dédié pour l'instant)."""

    name = models.CharField("nom", max_length=100)
    price_monthly = models.DecimalField("prix mensuel (FCFA)", max_digits=12, decimal_places=0, default=0)
    max_boutiques = models.PositiveIntegerField(
        "nombre de boutiques max", null=True, blank=True, help_text="Laisser vide = illimité"
    )
    max_users = models.PositiveIntegerField(
        "nombre d'employés max", null=True, blank=True, help_text="Laisser vide = illimité"
    )
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField("proposé aux entreprises", default=True)

    class Meta:
        verbose_name = "offre d'abonnement"
        verbose_name_plural = "offres d'abonnement"
        ordering = ["price_monthly"]

    def __str__(self):
        return self.name


class Subscription(UUIDModel, TimeStampedModel):
    """Abonnement d'une entreprise — auto-géré : c'est l'administrateur de
    l'entreprise qui choisit son offre et renseigne lui-même la date
    d'expiration après un paiement effectué hors application (Mobile
    Money, virement...). Pas de passerelle de paiement pour l'instant."""

    compte = models.OneToOneField(
        "tenants.Compte", on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    started_at = models.DateField("débuté le", default=timezone.localdate)
    expires_at = models.DateField(
        "expire le", null=True, blank=True,
        help_text="Laisser vide pour une offre sans limite de durée (ex: plan gratuit).",
    )
    payment_reference = models.CharField(
        "référence de paiement", max_length=255, blank=True,
        help_text="Ex: référence Mobile Money, numéro de virement...",
    )
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "abonnement"
        verbose_name_plural = "abonnements"

    def __str__(self):
        return f"{self.compte.name} — {self.plan.name}"

    @property
    def is_active(self):
        return self.expires_at is None or self.expires_at >= timezone.localdate()
