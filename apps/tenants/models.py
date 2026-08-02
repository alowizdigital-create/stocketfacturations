import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel, UUIDModel


class Compte(UUIDModel, TimeStampedModel):
    """Une entreprise cliente indépendante de la plateforme (SaaS
    multi-entreprises) : toutes les données d'un Compte sont strictement
    isolées de celles des autres Comptes."""

    PLAN_GRATUIT = "GRATUIT"
    PLAN_STANDARD = "STANDARD"
    PLAN_CHOICES = [
        (PLAN_GRATUIT, "Gratuit"),
        (PLAN_STANDARD, "Standard"),
    ]

    name = models.CharField("nom de l'entreprise", max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default=PLAN_GRATUIT)

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
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(
        "boutique par défaut",
        default=False,
        help_text="Boutique sélectionnée automatiquement à la connexion pour cette entreprise.",
    )

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
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
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
