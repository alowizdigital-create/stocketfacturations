from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from apps.core.models import TimeStampedModel, UUIDModel
from apps.tenants.models import Boutique


class SyncLog(UUIDModel, TimeStampedModel):
    """Registre serveur d'idempotence des appels de synchronisation : une
    même clé `Idempotency-Key` pour un même endpoint/boutique renvoie la
    réponse déjà enregistrée au lieu de rejouer la logique métier — voir
    apps.sync.views."""

    PUSH = "PUSH"
    PULL = "PULL"
    DIRECTION_CHOICES = [(PUSH, "Push"), (PULL, "Pull")]

    boutique = models.ForeignKey(Boutique, on_delete=models.CASCADE, related_name="sync_logs")
    direction = models.CharField(max_length=4, choices=DIRECTION_CHOICES, default=PUSH)
    endpoint = models.CharField(max_length=100)
    idempotency_key = models.CharField(max_length=255)
    request_hash = models.CharField(max_length=64)
    status_code = models.PositiveSmallIntegerField(default=200)
    response_snapshot = models.JSONField(encoder=DjangoJSONEncoder)

    class Meta:
        verbose_name = "journal de synchronisation"
        verbose_name_plural = "journaux de synchronisation"
        constraints = [
            models.UniqueConstraint(
                fields=["boutique", "endpoint", "idempotency_key"], name="unique_synclog_key"
            ),
        ]
        indexes = [
            models.Index(fields=["boutique", "created_at"]),
        ]

    def __str__(self):
        return f"{self.direction} {self.endpoint} ({self.boutique}) — {self.idempotency_key}"


class DeviceActivation(models.Model):
    """État d'activation de CE poste offline — au plus une ligne en base
    locale. Absente = poste non activé, les requêtes sont redirigées vers
    l'écran d'activation (voir apps.core.middleware). Sans effet en mode
    en ligne (config.settings.IS_OFFLINE=False)."""

    boutique_id = models.UUIDField()
    boutique_name = models.CharField(max_length=255)
    compte_id = models.UUIDField()
    compte_name = models.CharField(max_length=255)
    # Jeton en clair — inévitable, ce poste doit le présenter à chaque
    # appel de synchro. Même niveau de confiance que le reste des données
    # métier déjà en clair dans ce SQLite local (clients, factures...).
    token = models.CharField(max_length=255)
    activated_at = models.DateTimeField(auto_now_add=True)
    # Mis à True dès qu'un cycle pull/push se voit opposer un 401/403 par le
    # serveur (jeton régénéré depuis les paramètres en ligne) — voir
    # apps.sync.activation.mark_token_invalid(). Tant que ce drapeau est
    # actif, CurrentTenantMiddleware renvoie ce poste vers l'écran
    # d'activation pour qu'il colle le nouveau jeton, plutôt que de
    # continuer à tenter des cycles voués à l'échec en silence.
    token_invalid = models.BooleanField(default=False)

    class Meta:
        verbose_name = "activation du poste"
        verbose_name_plural = "activation du poste"

    def __str__(self):
        return f"{self.boutique_name} (activé le {self.activated_at:%d/%m/%Y})"

    @classmethod
    def get_active(cls):
        return cls.objects.first()


class OutboxEntry(UUIDModel, TimeStampedModel):
    """File d'attente locale des écritures créées hors-ligne, à pousser
    vers le serveur — un enregistrement par transaction métier (ex: une
    vente entière = une seule entrée `sale_transaction`, pas une par
    ligne). Voir apps.sync.outbox pour la sérialisation/l'envoi."""

    CLIENT = "client"
    STOCK_MOVEMENT = "stock_movement"
    INVOICE = "invoice"
    PAYMENT = "payment"
    SALE_TRANSACTION = "sale_transaction"
    CATEGORY = "category"
    UNIT = "unit"
    PRODUCT = "product"
    KIND_CHOICES = [
        (CLIENT, "Client"),
        (STOCK_MOVEMENT, "Mouvement de stock"),
        (INVOICE, "Facture/devis"),
        (PAYMENT, "Paiement"),
        (SALE_TRANSACTION, "Vente"),
        (CATEGORY, "Catégorie"),
        (UNIT, "Unité"),
        (PRODUCT, "Produit"),
    ]

    PENDING = "PENDING"
    SENT = "SENT"
    ERROR = "ERROR"
    STATUS_CHOICES = [(PENDING, "En attente"), (SENT, "Envoyé"), (ERROR, "Erreur")]

    kind = models.CharField(max_length=20, choices=KIND_CHOICES)
    object_id = models.UUIDField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "entrée de file de synchronisation"
        verbose_name_plural = "entrées de file de synchronisation"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "kind", "created_at"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.object_id} ({self.get_status_display()})"


class SyncCursor(models.Model):
    """Curseur `?since=` par type de ressource tirée — avancé à partir du
    `server_time` de la réponse (jamais l'horloge locale, potentiellement
    décalée). Une ligne par ressource, voir apps.sync.pull."""

    resource = models.CharField(max_length=40, primary_key=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.resource} — {self.last_synced_at or 'jamais'}"
