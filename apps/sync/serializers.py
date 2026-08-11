from decimal import Decimal

from rest_framework import serializers

from apps.accounts.models import User
from apps.catalog.models import Category, Product, ProductBoutiquePrice, Unit
from apps.sales.models import Client, Invoice, Payment, TaxRate
from apps.stock.models import StockLevel, StockMovement
from apps.tenants.models import Boutique, ExchangeRate, Membership

# --------------------------------------------------------------------------
# Push — payloads d'écriture envoyés par un poste offline. Ce sont des
# Serializer "plats" (pas ModelSerializer) : ils ne correspondent pas 1:1
# aux modèles (lignes imbriquées, client optionnel, id/created_at
# facultatifs pour le rejeu idempotent) et ne créent jamais rien
# eux-mêmes — la création passe toujours par apps.sales.services /
# apps.stock.services, jamais par un `.save()` de serializer.
# --------------------------------------------------------------------------


class ClientPushSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    name = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    address = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    nif = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")


class StockMovementPushSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    created_at = serializers.DateTimeField(required=False)
    product_id = serializers.UUIDField()
    type = serializers.ChoiceField(choices=[c[0] for c in StockMovement.TYPE_CHOICES])
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    unit_cost = serializers.DecimalField(
        max_digits=12, decimal_places=0, required=False, allow_null=True
    )
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    created_by_user_id = serializers.UUIDField(required=False, allow_null=True)


class InvoiceLinePushSerializer(serializers.Serializer):
    product_id = serializers.UUIDField(required=False, allow_null=True)
    description = serializers.CharField(max_length=255)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    unit_price_ht = serializers.DecimalField(max_digits=12, decimal_places=0)
    tva_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, default=Decimal("0")
    )
    discount_amount = serializers.DecimalField(
        max_digits=12, decimal_places=0, required=False, default=Decimal("0")
    )


class InvoicePushSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    created_at = serializers.DateTimeField(required=False)
    type = serializers.ChoiceField(choices=[Invoice.DEVIS, Invoice.FACTURE], default=Invoice.DEVIS)
    client = ClientPushSerializer(required=False, allow_null=True)
    lines = InvoiceLinePushSerializer(many=True)
    discount_amount = serializers.DecimalField(
        max_digits=14, decimal_places=0, required=False, default=Decimal("0")
    )
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True, default="")
    issue_date = serializers.DateField(required=False)
    created_by_user_id = serializers.UUIDField(required=False, allow_null=True)


class PaymentPushSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False)
    created_at = serializers.DateTimeField(required=False)
    invoice_id = serializers.UUIDField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=0)
    method = serializers.ChoiceField(
        choices=[c[0] for c in Payment.METHOD_CHOICES], default=Payment.ESPECES
    )
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    created_by_user_id = serializers.UUIDField(required=False, allow_null=True)


class SaleTransactionPaymentSerializer(serializers.Serializer):
    """Paiement imbriqué dans un bundle vente — pas d'`invoice_id`, la
    facture est générée dans le même appel (voir SaleTransactionPushSerializer)."""

    id = serializers.UUIDField(required=False)
    created_at = serializers.DateTimeField(required=False)
    amount = serializers.DecimalField(max_digits=14, decimal_places=0)
    method = serializers.ChoiceField(
        choices=[c[0] for c in Payment.METHOD_CHOICES], default=Payment.ESPECES
    )
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")


class SaleLinePushSerializer(serializers.Serializer):
    product_id = serializers.UUIDField(required=False, allow_null=True)
    description = serializers.CharField(max_length=255)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    unit_price_ht = serializers.DecimalField(max_digits=12, decimal_places=0)
    tva_rate = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, default=Decimal("0")
    )
    stock_movement_id = serializers.UUIDField(required=False, allow_null=True)


class SaleTransactionPushSerializer(serializers.Serializer):
    """Bundle vente complet : mirroir de `sale_create` (build_sale ->
    confirm_sale -> generate_invoice_from_sale -> record_payment) en une
    seule transaction par item — voir apps.sales.views.sale_create."""

    id = serializers.UUIDField(required=False)
    created_at = serializers.DateTimeField(required=False)
    sale_date = serializers.DateField(required=False)
    currency = serializers.CharField(max_length=3, required=False, allow_blank=True, default="")
    client = ClientPushSerializer(required=False, allow_null=True)
    lines = SaleLinePushSerializer(many=True)
    generate_invoice = serializers.BooleanField(default=True)
    invoice_id = serializers.UUIDField(required=False, allow_null=True)
    payment = SaleTransactionPaymentSerializer(required=False, allow_null=True)
    created_by_user_id = serializers.UUIDField(required=False, allow_null=True)


# --------------------------------------------------------------------------
# Pull — données de référence en lecture seule, tirées par le poste
# offline. `updated_at` est exposé partout pour permettre le filtrage
# incrémental `?since=` (voir apps.sync.views).
# --------------------------------------------------------------------------


class BoutiquePullSerializer(serializers.ModelSerializer):
    compte_id = serializers.UUIDField(source="compte.id", read_only=True)
    compte_name = serializers.CharField(source="compte.name", read_only=True)

    class Meta:
        model = Boutique
        fields = [
            "id", "name", "code", "address", "phone", "devise",
            "country_calling_code", "om_account_name", "om_number",
            "momo_account_name", "momo_number", "is_default",
            "compte_id", "compte_name", "updated_at",
        ]


class UserPullSerializer(serializers.ModelSerializer):
    """`password` = le hachage Django (jamais le mot de passe en clair) —
    permet à un caissier de se connecter hors-ligne avec exactement le
    même email/mot de passe qu'en ligne, sans nouveau mécanisme d'auth."""

    class Meta:
        model = User
        fields = ["id", "email", "password", "first_name", "last_name", "is_active", "updated_at"]


class MembershipPullSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_first_name = serializers.CharField(source="user.first_name", read_only=True)
    user_last_name = serializers.CharField(source="user.last_name", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id", "user_id", "user_email", "user_first_name", "user_last_name",
            "role", "is_active", "updated_at",
        ]


class ClientPullSerializer(serializers.ModelSerializer):
    """Clients créés côté web (ou par un autre poste offline via push) —
    redescendus vers ce poste pour qu'un caissier puisse les retrouver
    hors-ligne, pas seulement ceux qu'il a lui-même créés localement."""

    class Meta:
        model = Client
        fields = ["id", "name", "phone", "email", "address", "nif", "updated_at"]


class ExchangeRatePullSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExchangeRate
        fields = ["id", "currency", "rate", "updated_at"]


class TaxRatePullSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaxRate
        fields = ["id", "name", "rate", "is_default", "active_from", "updated_at"]


class CategoryPullSerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "parent_id", "updated_at"]


class UnitPullSerializer(serializers.ModelSerializer):
    class Meta:
        model = Unit
        fields = ["id", "name", "symbol", "updated_at"]


class ProductPullSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "category_id", "name", "sku", "barcode", "image_url",
            "unit_id", "default_sale_price", "tva_rate",
            "low_stock_threshold_default", "is_active", "updated_at",
        ]

    def get_image_url(self, obj):
        if not obj.image:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.image.url) if request else obj.image.url


class StockLevelPullSerializer(serializers.ModelSerializer):
    """Snapshot du cache StockLevel, pas du registre StockMovement complet
    — le poste offline part de cette photo puis fait évoluer son propre
    cache localement au fil des ventes (voir apps.stock.services.apply_movement),
    jusqu'au prochain pull qui recale sur le serveur."""

    class Meta:
        model = StockLevel
        fields = ["product_id", "quantity", "updated_at"]


class ProductBoutiquePricePullSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductBoutiquePrice
        fields = [
            "id", "product_id", "boutique_id", "price_override",
            "low_stock_threshold_override", "is_available", "updated_at",
        ]
