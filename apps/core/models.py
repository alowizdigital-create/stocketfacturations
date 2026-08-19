import secrets
import uuid

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class UUIDModel(models.Model):
    """Clé primaire UUID générée côté client — indispensable pour que les
    enregistrements créés hors-ligne aient un identifiant valable
    immédiatement et puissent être synchronisés par upsert idempotent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    # default=timezone.now (et non auto_now_add) : un enregistrement créé
    # hors-ligne doit pouvoir transporter l'heure de l'appareil local telle
    # quelle lors de la synchronisation, plutôt que l'heure du serveur.
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True


class BoutiqueScopedQuerySet(models.QuerySet):
    def for_boutique(self, boutique):
        return self.filter(boutique=boutique)


class BoutiqueScopedManager(models.Manager.from_queryset(BoutiqueScopedQuerySet)):
    pass


class BoutiqueScopedModel(models.Model):
    """Base pour tout modèle métier rattaché à une boutique précise."""

    boutique = models.ForeignKey(
        "tenants.Boutique", on_delete=models.PROTECT, related_name="+"
    )

    objects = BoutiqueScopedManager()

    class Meta:
        abstract = True


class CompteScopedModel(models.Model):
    """Base pour les modèles rattachés à une entreprise (Compte) mais
    partagés entre ses boutiques (ex: catalogue, taux de TVA)."""

    compte = models.ForeignKey(
        "tenants.Compte", on_delete=models.CASCADE, related_name="+"
    )

    class Meta:
        abstract = True


# Exclut les caractères visuellement ambigus (0/O, 1/l/I) — surtout utile
# si un client recopie le code à la main plutôt que de taper le lien.
_SHORT_CODE_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ"


def _generate_short_code(length=6):
    return "".join(secrets.choice(_SHORT_CODE_ALPHABET) for _ in range(length))


class ShortLink(models.Model):
    """Redirection code court -> chemin relatif. Génère des liens type
    /s/aB3xY9/ pour raccourcir les URL envoyées aux clients (WhatsApp) —
    volontairement auto-hébergé (pas un service tiers) : fonctionne aussi
    hors-ligne, et n'expose aucune donnée cliente vers un tiers. Générique
    (pas spécifique aux factures), réutilisable pour tout futur lien à
    raccourcir."""

    code = models.CharField(max_length=10, unique=True, editable=False)
    target_path = models.CharField(max_length=500, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("lien court")
        verbose_name_plural = _("liens courts")

    def __str__(self):
        return self.code

    @classmethod
    def get_or_create_for_path(cls, target_path):
        existing = cls.objects.filter(target_path=target_path).first()
        if existing:
            return existing
        for _attempt in range(5):
            obj, created = cls.objects.get_or_create(
                code=_generate_short_code(), defaults={"target_path": target_path}
            )
            if created:
                return obj
        raise RuntimeError("Impossible de générer un code court unique.")
