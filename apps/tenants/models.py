import hashlib
import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.images import ensure_webp
from apps.core.models import TimeStampedModel, UUIDModel


def _join_cities(cities):
    """« A », « A et B », « A, B et C »."""
    if len(cities) <= 1:
        return "".join(cities)
    return ", ".join(cities[:-1]) + " " + str(_("et")) + " " + cities[-1]


class Compte(UUIDModel, TimeStampedModel):
    """Une entreprise cliente indépendante de la plateforme (SaaS
    multi-entreprises) : toutes les données d'un Compte sont strictement
    isolées de celles des autres Comptes."""

    name = models.CharField(_("nom de l'entreprise"), max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    logo = models.ImageField(
        _("logo de l'entreprise"), upload_to="logos/", null=True, blank=True,
        help_text=_("Affiché en haut des factures/devis (ticket 80 mm)."),
    )

    niu = models.CharField(
        _("NIU"), max_length=50, blank=True,
        help_text=_("Numéro d'identifiant unique (contribuable) — affiché sur les factures et devis."),
    )
    rccm = models.CharField(
        _("RCCM"), max_length=50, blank=True,
        help_text=_("Registre du commerce et du crédit mobilier — affiché sur les factures et devis."),
    )

    class Meta:
        verbose_name = _("entreprise")
        verbose_name_plural = _("entreprises")

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        ensure_webp(self.logo)
        super().save(*args, **kwargs)


class Boutique(UUIDModel, TimeStampedModel):
    compte = models.ForeignKey(Compte, on_delete=models.CASCADE, related_name="boutiques")
    name = models.CharField(_("nom de la boutique"), max_length=255)
    code = models.CharField(
        _("code court"),
        max_length=10,
        help_text=_("Préfixe utilisé pour numéroter les factures hors-ligne (ex: BTQ-001)."),
    )
    address = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    devise = models.CharField(max_length=3, default="XOF")
    country_calling_code = models.CharField(
        _("indicatif téléphonique du pays"),
        max_length=4,
        blank=True,
        help_text=_(
            "Complète automatiquement les numéros WhatsApp saisis sans "
            "indicatif (ex: 07... devient +225 07...)."
        ),
    )
    tva_rate = models.DecimalField(
        _("TVA par défaut (%)"), max_digits=5, decimal_places=2, default=0,
        help_text=_(
            "Proposée sur les nouvelles lignes de facture/devis et les nouveaux produits "
            "(ex: 19,25 au Cameroun). Modifiable ligne par ligne."
        ),
    )
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        _("boutique par défaut"),
        default=False,
        help_text=_("Boutique sélectionnée automatiquement à la connexion pour cette entreprise."),
    )

    # Moyens de paiement Mobile Money — affichés sur les factures/devis
    # (PDF et page publique) pour que le client sache où payer. Vides par
    # défaut : rien ne s'affiche tant que la boutique ne les a pas remplis.
    om_account_name = models.CharField(_("Orange Money — nom du compte"), max_length=100, blank=True)
    om_number = models.CharField(_("Orange Money — numéro"), max_length=30, blank=True)
    momo_account_name = models.CharField(_("MTN MoMo — nom du compte"), max_length=100, blank=True)
    momo_number = models.CharField(_("MTN MoMo — numéro"), max_length=30, blank=True)

    # Montant fixe déduit de la caisse individuelle du livreur à chaque
    # commande marquée livrée (voir apps.cashier.services.charge_delivery_fee,
    # appelé depuis apps.sales.services.mark_commande_delivered). Vide =
    # aucune déduction automatique.
    delivery_price = models.DecimalField(
        _("prix de livraison"), max_digits=14, decimal_places=0, null=True, blank=True,
        help_text=_(
            "Déduit automatiquement de la caisse individuelle de la personne qui livre, à chaque "
            "commande marquée « Livrée ». Laisser vide pour ne rien déduire."
        ),
    )

    # Boutique en ligne (vitrine publique, voir apps.storefront) : désactivée
    # par défaut, adresse /boutique/<shop_slug>/ choisie par le vendeur.
    shop_enabled = models.BooleanField(_("boutique en ligne active"), default=False)
    shop_slug = models.SlugField(
        _("adresse de la boutique en ligne"), max_length=60, unique=True, null=True, blank=True,
        help_text=_("Lettres minuscules, chiffres et tirets — ex: ma-boutique."),
    )
    shop_description = models.CharField(
        _("présentation"), max_length=300, blank=True,
        help_text=_("Une phrase affichée en haut de la boutique en ligne."),
    )
    shop_order_notice = models.CharField(
        _("message avant commande"), max_length=400, blank=True,
        help_text=_(
            "Facultatif : un avertissement affiché au-dessus du bouton « Commander » "
            "(ex: « Merci de ne commander que si vous êtes disponible pour la livraison »)."
        ),
    )
    shop_checkout_notice = models.CharField(
        _("message « IMPORTANT » du formulaire"), max_length=500, blank=True,
        help_text=_(
            "Facultatif : affiché dans un encadré « IMPORTANT » du formulaire de commande "
            "(ex: « En cliquant sur le bouton, vous confirmez que vous êtes disponible pour être livré. »)."
        ),
    )
    shop_order_footer = models.CharField(
        _("message sous le bouton de commande"), max_length=300, blank=True,
        help_text=_("Facultatif (ex: « Nous vous contacterons dans les 30 min après votre commande. Merci ! »)."),
    )
    shop_delivery_fee = models.DecimalField(
        _("frais de livraison"), max_digits=10, decimal_places=0, default=0,
        help_text=_("Utilisés seulement si aucune zone de livraison n'est définie. 0 = livraison gratuite."),
    )
    shop_delivery_info = models.CharField(
        _("informations de livraison"), max_length=200, blank=True,
        help_text=_("Laissez vide pour générer le bandeau automatiquement à partir de vos zones de livraison."),
    )

    class Meta:
        verbose_name = _("boutique")
        verbose_name_plural = _("boutiques")
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
    def delivery_banner(self):
        """Texte du bandeau de livraison de la vitrine. Un texte saisi à la
        main (shop_delivery_info) l'emporte ; sinon il est construit à partir
        des zones de livraison — « Livraison gratuite à Yaoundé et Douala »,
        « 1 000 XAF à Bafoussam » — puis, sans zone, des frais uniques."""
        if self.shop_delivery_info:
            return self.shop_delivery_info
        zones = list(self.delivery_zones.all())
        if not zones:
            if self.shop_delivery_fee:
                return _("Frais de livraison : %(fee)s %(devise)s") % {
                    "fee": f"{self.shop_delivery_fee:,.0f}".replace(",", " "), "devise": self.devise,
                }
            return ""
        free = [z.city for z in zones if not z.fee]
        parts = []
        if free:
            parts.append(_("Livraison gratuite à %(cities)s") % {"cities": _join_cities(free)})
        by_fee = {}
        for zone in zones:
            if zone.fee:
                by_fee.setdefault(zone.fee, []).append(zone.city)
        for fee, cities in by_fee.items():
            amount = f"{fee:,.0f}".replace(",", " ")
            parts.append(_("%(amount)s %(devise)s à %(cities)s") % {
                "amount": amount, "devise": self.devise, "cities": _join_cities(cities),
            })
        if not free and parts:
            parts[0] = _("Livraison : ") + parts[0]
        return " · ".join(parts)

    @property
    def legal_mentions(self):
        """Mentions légales de l'entreprise (NIU, RCCM) renseignées, prêtes
        à afficher sur une facture/devis — liste vide si rien n'est saisi."""
        mentions = []
        if self.compte.niu:
            mentions.append(f"NIU : {self.compte.niu}")
        if self.compte.rccm:
            mentions.append(f"RCCM : {self.compte.rccm}")
        return mentions

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


class DeliveryZone(UUIDModel, TimeStampedModel):
    """Ville desservie par la boutique en ligne, avec son tarif de livraison
    (0 = gratuite). Le bandeau de la vitrine est généré à partir de ces
    zones (voir Boutique.delivery_banner) et le client choisit sa ville à la
    commande."""

    boutique = models.ForeignKey(Boutique, on_delete=models.CASCADE, related_name="delivery_zones")
    city = models.CharField(_("ville / zone"), max_length=100)
    fee = models.DecimalField(
        _("frais de livraison"), max_digits=10, decimal_places=0, default=0,
        help_text=_("0 = livraison gratuite."),
    )

    class Meta:
        verbose_name = _("zone de livraison")
        verbose_name_plural = _("zones de livraison")
        ordering = ["fee", "city"]
        constraints = [
            models.UniqueConstraint(fields=["boutique", "city"], name="unique_delivery_zone_par_boutique"),
        ]

    def __str__(self):
        return f"{self.city} ({self.fee})"


class ExchangeRate(UUIDModel, TimeStampedModel):
    """Taux de change d'une devise étrangère par rapport à la devise par
    défaut de la boutique (ex: 1 EUR = 655,957 XOF) — permet de proposer
    une vente/un devis dans une autre devise avec conversion automatique
    des montants."""

    boutique = models.ForeignKey(Boutique, on_delete=models.CASCADE, related_name="exchange_rates")
    currency = models.CharField(_("devise"), max_length=3)
    rate = models.DecimalField(
        _("taux"),
        max_digits=14,
        decimal_places=4,
        help_text=_("Valeur de 1 unité de cette devise, exprimée dans la devise de la boutique. Ex: 1 EUR = 655.9570 XOF."),
    )

    class Meta:
        verbose_name = _("taux de change")
        verbose_name_plural = _("taux de change")
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
        (ADMIN_COMPTE, _("Administrateur de l'entreprise")),
        (GERANT_BOUTIQUE, _("Gérant de boutique")),
        (CAISSIER, _("Caissier")),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    boutique = models.ForeignKey(Boutique, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _("affectation")
        verbose_name_plural = _("affectations")
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
        verbose_name = _("jeton API boutique")
        verbose_name_plural = _("jetons API boutique")

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

    name = models.CharField(_("nom"), max_length=100)
    price_monthly = models.DecimalField(_("prix mensuel (FCFA)"), max_digits=12, decimal_places=0, default=0)
    max_boutiques = models.PositiveIntegerField(
        _("nombre de boutiques max"), null=True, blank=True, help_text=_("Laisser vide = illimité")
    )
    max_users = models.PositiveIntegerField(
        _("nombre d'employés max"), null=True, blank=True, help_text=_("Laisser vide = illimité")
    )
    description = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(_("proposé aux entreprises"), default=True)

    class Meta:
        verbose_name = _("offre d'abonnement")
        verbose_name_plural = _("offres d'abonnement")
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
    started_at = models.DateField(_("débuté le"), default=timezone.localdate)
    expires_at = models.DateField(
        _("expire le"), null=True, blank=True,
        help_text=_("Laisser vide pour une offre sans limite de durée (ex: plan gratuit)."),
    )
    payment_reference = models.CharField(
        _("référence de paiement"), max_length=255, blank=True,
        help_text=_("Ex: référence Mobile Money, numéro de virement..."),
    )
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _("abonnement")
        verbose_name_plural = _("abonnements")

    def __str__(self):
        return f"{self.compte.name} — {self.plan.name}"

    @property
    def is_active(self):
        return self.expires_at is None or self.expires_at >= timezone.localdate()
