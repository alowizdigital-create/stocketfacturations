import uuid

from django.db import models
from django.utils import timezone


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
