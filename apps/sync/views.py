import hashlib
import json
from functools import wraps

import requests
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics, pagination, status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.catalog.models import Category, Product, ProductBoutiquePrice, Unit
from apps.sales import services as sales_services
from apps.sales.models import Client, Payment, Sale, TaxRate
from apps.stock import services as stock_services
from apps.stock.models import StockLevel, StockMovement
from apps.tenants.models import BoutiqueAPIToken, ExchangeRate, Membership

from .client import SyncClient
from .forms import ActivationForm
from .models import DeviceActivation, SyncLog
from .pull import run_pull_cycle
from .serializers import (
    BoutiquePullSerializer,
    CategoryPullSerializer,
    ClientPullSerializer,
    ClientPushSerializer,
    ExchangeRatePullSerializer,
    InvoicePushSerializer,
    MembershipPullSerializer,
    PaymentPushSerializer,
    ProductBoutiquePricePullSerializer,
    ProductPullSerializer,
    SaleTransactionPushSerializer,
    StockLevelPullSerializer,
    StockMovementPushSerializer,
    TaxRatePullSerializer,
    UnitPullSerializer,
    UserPullSerializer,
)

IDEMPOTENCY_HEADER = "Idempotency-Key"


class BoutiqueScopedMixin:
    """Pose `self.boutique`/`self.compte` depuis le principal résolu par
    `BoutiqueTokenAuthentication` — jamais depuis le corps de la requête."""

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.boutique = request.user.boutique
        self.compte = request.user.compte


class PingView(BoutiqueScopedMixin, APIView):
    def get(self, request):
        return Response({
            "boutique": {"id": str(self.boutique.id), "name": self.boutique.name},
            "compte": {"id": str(self.compte.id), "name": self.compte.name},
            "server_time": timezone.now(),
            "api_version": "v1",
        })


# --------------------------------------------------------------------------
# Push
# --------------------------------------------------------------------------


def _resolve_product(product_id):
    if not product_id:
        return None
    return Product.objects.filter(pk=product_id).first()


def _resolve_line_product(product_id):
    """Comme `_resolve_product`, mais un `product_id` fourni-mais-inconnu
    est une erreur explicite plutôt qu'une ligne libre silencieuse — une
    ligne de vente/facture qui référence un produit non résolu ne doit
    jamais passer inaperçue (elle ne décompterait aucun stock)."""

    if not product_id:
        return None
    product = Product.objects.filter(pk=product_id).first()
    if product is None:
        raise ValueError(f"product_id {product_id} inconnu.")
    return product


def _resolve_created_by(boutique, user_id):
    """N'accorde `created_by` que si l'utilisateur a une affectation active
    sur cette boutique précise — jamais fait confiance à un id arbitraire
    envoyé par le poste offline."""

    if not user_id:
        return None
    if not Membership.objects.filter(user_id=user_id, boutique=boutique, is_active=True).exists():
        return None
    return User.objects.filter(pk=user_id).first()


class BasePushView(BoutiqueScopedMixin, APIView):
    endpoint_name = None
    item_serializer_class = None

    def handle_item(self, boutique, data):
        raise NotImplementedError

    def post(self, request):
        idempotency_key = request.headers.get(IDEMPOTENCY_HEADER)
        if not idempotency_key:
            return Response(
                {"detail": f"En-tête {IDEMPOTENCY_HEADER} requis."}, status=status.HTTP_400_BAD_REQUEST
            )

        body_hash = hashlib.sha256(
            json.dumps(request.data, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()

        existing = SyncLog.objects.filter(
            boutique=self.boutique, endpoint=self.endpoint_name, idempotency_key=idempotency_key,
        ).first()
        if existing is not None:
            if existing.request_hash != body_hash:
                return Response(
                    {"detail": "Idempotency-Key déjà utilisée avec un corps différent."},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response(existing.response_snapshot, status=existing.status_code)

        items = request.data.get("items", [])
        results = []
        for raw_item in items:
            serializer = self.item_serializer_class(data=raw_item)
            if not serializer.is_valid():
                results.append({
                    "id": raw_item.get("id"), "status": "error", "detail": serializer.errors,
                })
                continue
            try:
                with transaction.atomic():
                    result = self.handle_item(self.boutique, serializer.validated_data)
                results.append(result)
            except Exception as exc:  # noqa: BLE001 — un item invalide ne doit jamais bloquer le lot
                results.append({
                    "id": str(raw_item.get("id", "")), "status": "error", "detail": str(exc),
                })

        response_body = {"idempotency_key": idempotency_key, "results": results}
        SyncLog.objects.create(
            boutique=self.boutique, direction=SyncLog.PUSH, endpoint=self.endpoint_name,
            idempotency_key=idempotency_key, request_hash=body_hash,
            status_code=status.HTTP_200_OK, response_snapshot=response_body,
        )
        BoutiqueAPIToken.objects.filter(pk=request.auth.pk).update(last_push_at=timezone.now())
        return Response(response_body, status=status.HTTP_200_OK)


class PushClientsView(BasePushView):
    endpoint_name = "push.clients"
    item_serializer_class = ClientPushSerializer

    def handle_item(self, boutique, data):
        from apps.sales.models import Client

        item_id = data.get("id")
        was_existing = item_id is not None and Client.objects.filter(pk=item_id, boutique=boutique).exists()
        client = sales_services.get_or_create_client(
            boutique=boutique,
            id=item_id,
            name=data["name"],
            phone=data.get("phone", ""),
            email=data.get("email", ""),
            address=data.get("address", ""),
            nif=data.get("nif", ""),
        )
        return {"id": str(client.id), "status": "duplicate" if was_existing else "created"}


class PushStockMovementsView(BasePushView):
    endpoint_name = "push.stock_movements"
    item_serializer_class = StockMovementPushSerializer

    def handle_item(self, boutique, data):
        item_id = data.get("id")
        was_existing = item_id is not None and StockMovement.objects.filter(pk=item_id).exists()
        product = _resolve_product(data["product_id"])
        if product is None:
            return {"id": str(item_id) if item_id else "", "status": "error", "detail": "product_id inconnu."}

        movement = stock_services.apply_movement(
            boutique=boutique,
            product=product,
            type=data["type"],
            quantity=data["quantity"],
            unit_cost=data.get("unit_cost"),
            reason=data.get("reason", ""),
            created_by=_resolve_created_by(boutique, data.get("created_by_user_id")),
            source=StockMovement.SOURCE_OFFLINE,
            movement_id=item_id,
            created_at=data.get("created_at"),
        )
        return {"id": str(movement.id), "status": "duplicate" if was_existing else "created"}


class PushInvoicesView(BasePushView):
    endpoint_name = "push.invoices"
    item_serializer_class = InvoicePushSerializer

    def handle_item(self, boutique, data):
        from apps.sales.models import Invoice

        item_id = data.get("id")
        was_existing = item_id is not None and Invoice.objects.filter(pk=item_id).exists()

        client = None
        if data.get("client"):
            client = sales_services.get_or_create_client(
                boutique=boutique,
                id=data["client"].get("id"),
                name=data["client"]["name"],
                phone=data["client"].get("phone", ""),
                email=data["client"].get("email", ""),
                address=data["client"].get("address", ""),
                nif=data["client"].get("nif", ""),
            )

        lines_data = [
            {
                "product": _resolve_line_product(line.get("product_id")),
                "description": line["description"],
                "quantity": line["quantity"],
                "unit_price_ht": line["unit_price_ht"],
                "tva_rate": line.get("tva_rate", 0),
                "discount_amount": line.get("discount_amount", 0),
            }
            for line in data["lines"]
        ]

        invoice = sales_services.build_invoice(
            boutique=boutique,
            client=client,
            type=data.get("type", Invoice.DEVIS),
            created_by=_resolve_created_by(boutique, data.get("created_by_user_id")),
            lines_data=lines_data,
            issue_date=data.get("issue_date"),
            discount_amount=data.get("discount_amount", 0),
            currency=data.get("currency") or None,
            id=item_id,
            created_at=data.get("created_at"),
        )
        return {"id": str(invoice.id), "status": "duplicate" if was_existing else "created"}


class PushPaymentsView(BasePushView):
    endpoint_name = "push.payments"
    item_serializer_class = PaymentPushSerializer

    def handle_item(self, boutique, data):
        from apps.sales.models import Invoice

        item_id = data.get("id")
        was_existing = item_id is not None and Payment.objects.filter(pk=item_id).exists()

        invoice = Invoice.objects.filter(pk=data["invoice_id"], boutique=boutique).first()
        if invoice is None:
            return {
                "id": str(item_id) if item_id else "", "status": "error",
                "detail": "invoice_id inconnu pour cette boutique.",
            }

        sales_services.record_payment(
            invoice,
            amount=data["amount"],
            method=data.get("method", Payment.ESPECES),
            reference=data.get("reference", ""),
            created_by=_resolve_created_by(boutique, data.get("created_by_user_id")),
            id=item_id,
            created_at=data.get("created_at"),
        )
        return {"id": str(item_id) if item_id else "", "status": "duplicate" if was_existing else "created"}


class PushSaleTransactionsView(BasePushView):
    endpoint_name = "push.sale_transactions"
    item_serializer_class = SaleTransactionPushSerializer

    def handle_item(self, boutique, data):
        item_id = data.get("id")
        was_existing = item_id is not None and Sale.objects.filter(pk=item_id).exists()

        client = None
        if data.get("client"):
            client = sales_services.get_or_create_client(
                boutique=boutique,
                id=data["client"].get("id"),
                name=data["client"]["name"],
                phone=data["client"].get("phone", ""),
                email=data["client"].get("email", ""),
                address=data["client"].get("address", ""),
                nif=data["client"].get("nif", ""),
            )

        created_by = _resolve_created_by(boutique, data.get("created_by_user_id"))

        lines_data = []
        movement_ids = []
        for line in data["lines"]:
            product = _resolve_line_product(line.get("product_id"))
            lines_data.append({
                "product": product,
                "description": line["description"],
                "quantity": line["quantity"],
                "unit_price_ht": line["unit_price_ht"],
                "tva_rate": line.get("tva_rate", 0),
            })
            movement_ids.append(line.get("stock_movement_id") if product else None)

        sale = sales_services.build_sale(
            boutique=boutique,
            client=client,
            created_by=created_by,
            lines_data=lines_data,
            sale_date=data.get("sale_date"),
            currency=data.get("currency") or None,
            id=item_id,
            created_at=data.get("created_at"),
        )
        sale = sales_services.confirm_sale(
            sale, created_by=created_by,
            movement_ids=movement_ids if any(movement_ids) else None,
            created_at=data.get("created_at"),
            source=StockMovement.SOURCE_OFFLINE,
        )

        invoice = None
        if data.get("generate_invoice", True):
            invoice = sales_services.generate_invoice_from_sale(
                sale, created_by=created_by, id=data.get("invoice_id"), created_at=data.get("created_at"),
            )
            payment_data = data.get("payment")
            if invoice is not None and payment_data:
                sales_services.record_payment(
                    invoice,
                    amount=payment_data["amount"],
                    method=payment_data.get("method", Payment.ESPECES),
                    reference=payment_data.get("reference", ""),
                    created_by=created_by,
                    id=payment_data.get("id"),
                    created_at=payment_data.get("created_at"),
                )

        return {
            "id": str(sale.id),
            "status": "duplicate" if was_existing else "created",
            "invoice_id": str(invoice.id) if invoice else None,
        }


# --------------------------------------------------------------------------
# Pull
# --------------------------------------------------------------------------


class SyncCursorPagination(pagination.CursorPagination):
    ordering = ("updated_at", "id")
    page_size = 200


class BasePullView(BoutiqueScopedMixin, generics.ListAPIView):
    pagination_class = SyncCursorPagination

    def get_base_queryset(self):
        raise NotImplementedError

    def get_queryset(self):
        qs = self.get_base_queryset()
        since = self.request.query_params.get("since")
        if since:
            since_dt = parse_datetime(since)
            if since_dt is None:
                raise ValidationError({"since": "Format ISO 8601 attendu."})
            qs = qs.filter(updated_at__gt=since_dt)
        return qs

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data["server_time"] = timezone.now()
        BoutiqueAPIToken.objects.filter(pk=request.auth.pk).update(last_pull_at=timezone.now())
        return response


class PullBoutiqueView(BoutiqueScopedMixin, APIView):
    def get(self, request):
        data = BoutiquePullSerializer(self.boutique).data
        data["server_time"] = timezone.now()
        BoutiqueAPIToken.objects.filter(pk=request.auth.pk).update(last_pull_at=timezone.now())
        return Response(data)


class PullUsersView(BasePullView):
    """Scopé à CETTE boutique (pas tout le compte comme
    `PullMembershipsView`) : réduit le rayon d'exposition des hachages de
    mot de passe en cas de compromission d'un poste — seuls les
    identifiants des utilisateurs pouvant réellement se connecter sur ce
    poste précis quittent le serveur."""

    serializer_class = UserPullSerializer

    def get_base_queryset(self):
        return User.objects.filter(
            memberships__boutique=self.boutique, memberships__is_active=True
        ).distinct()


class PullMembershipsView(BasePullView):
    serializer_class = MembershipPullSerializer

    def get_base_queryset(self):
        return Membership.objects.filter(boutique__compte=self.compte, is_active=True).select_related("user")


class PullClientsView(BasePullView):
    """Redescend les clients créés côté web (ou par un autre poste offline)
    — pas seulement ceux créés par ce poste précis. Sans ça, un caissier
    hors-ligne ne retrouve jamais un client enregistré en ligne."""

    serializer_class = ClientPullSerializer

    def get_base_queryset(self):
        return Client.objects.filter(boutique=self.boutique)


class PullExchangeRatesView(BasePullView):
    serializer_class = ExchangeRatePullSerializer

    def get_base_queryset(self):
        return ExchangeRate.objects.filter(boutique=self.boutique)


class PullTaxRatesView(BasePullView):
    serializer_class = TaxRatePullSerializer

    def get_base_queryset(self):
        return TaxRate.objects.filter(compte=self.compte)


class PullCategoriesView(BasePullView):
    serializer_class = CategoryPullSerializer

    def get_base_queryset(self):
        return Category.objects.filter(compte=self.compte)


class PullUnitsView(BasePullView):
    serializer_class = UnitPullSerializer

    def get_base_queryset(self):
        return Unit.objects.filter(compte=self.compte)


class PullProductsView(BasePullView):
    serializer_class = ProductPullSerializer

    def get_base_queryset(self):
        return Product.objects.filter(compte=self.compte)


class PullProductBoutiquePricesView(BasePullView):
    serializer_class = ProductBoutiquePricePullSerializer

    def get_base_queryset(self):
        return ProductBoutiquePrice.objects.filter(boutique=self.boutique)


class PullStockLevelsView(BasePullView):
    """Photo du cache StockLevel — pas le registre StockMovement complet,
    voir StockLevelPullSerializer. Indispensable pour que la recherche
    produit de la vente offline (qui exclut les ruptures de stock) trouve
    des produits vendables dès l'activation."""

    serializer_class = StockLevelPullSerializer

    def get_base_queryset(self):
        return StockLevel.objects.filter(boutique=self.boutique)


# --------------------------------------------------------------------------
# Activation du poste offline — écran local (pas d'authentification par
# jeton : c'est justement l'étape qui n'a pas encore de jeton local).
# --------------------------------------------------------------------------


def _require_offline(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not settings.IS_OFFLINE:
            raise Http404()
        return view_func(request, *args, **kwargs)
    return wrapped


@_require_offline
def activate_device(request):
    if DeviceActivation.get_active() is not None:
        # Pas de réécrasement : mélanger les données de deux boutiques
        # dans le même SQLite n'est pas géré. Récupération = supprimer le
        # dossier de données du poste et remigrer.
        return redirect("accounts:login")

    form = ActivationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        raw_token = form.cleaned_data["token"].strip()
        try:
            client = SyncClient(base_url=settings.SYNC_BASE_URL, token=raw_token)
            data = client.ping()
        except requests.RequestException as exc:
            form.add_error(None, f"Connexion au serveur impossible : {exc}")
        else:
            DeviceActivation.objects.create(
                boutique_id=data["boutique"]["id"],
                boutique_name=data["boutique"]["name"],
                compte_id=data["compte"]["id"],
                compte_name=data["compte"]["name"],
                token=raw_token,
            )
            run_pull_cycle(client)
            messages.success(request, "Poste activé et données synchronisées.")
            return redirect("accounts:login")

    return render(request, "sync/activate.html", {"form": form})
