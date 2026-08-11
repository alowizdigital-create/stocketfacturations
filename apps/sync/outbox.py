import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from .activation import get_active_client
from .models import OutboxEntry

BATCH_SIZE = 50


def enqueue(kind, object_id):
    """Met en file d'attente un enregistrement créé localement pour la
    prochaine synchro — no-op en mode en ligne. Un seul appel par
    transaction métier (ex: une vente entière = un seul enqueue), jamais
    un par étape de service — voir les points d'appel dans
    apps.sales.views/apps.stock.views."""

    if not settings.IS_OFFLINE:
        return None
    return OutboxEntry.objects.create(kind=kind, object_id=object_id)


def _serialize_client(object_id):
    from apps.sales.models import Client

    client = Client.objects.get(pk=object_id)
    return {
        "id": str(client.id), "name": client.name, "phone": client.phone,
        "email": client.email, "address": client.address, "nif": client.nif,
    }


def _serialize_stock_movement(object_id):
    from apps.stock.models import StockMovement

    movement = StockMovement.objects.get(pk=object_id)
    return {
        "id": str(movement.id),
        "created_at": movement.created_at.isoformat(),
        "product_id": str(movement.product_id),
        "type": movement.type,
        "quantity": str(movement.quantity),
        "unit_cost": str(movement.unit_cost) if movement.unit_cost is not None else None,
        "reason": movement.reason,
        "created_by_user_id": str(movement.created_by_id) if movement.created_by_id else None,
    }


def _serialize_invoice(object_id):
    from apps.sales.models import Invoice

    invoice = Invoice.objects.select_related("client").prefetch_related("lines").get(pk=object_id)
    payload = {
        "id": str(invoice.id),
        "created_at": invoice.created_at.isoformat(),
        "type": invoice.type,
        "lines": [
            {
                "product_id": str(line.product_id) if line.product_id else None,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit_price_ht": str(line.unit_price_ht),
                "tva_rate": str(line.tva_rate),
                "discount_amount": str(line.discount_amount),
            }
            for line in invoice.lines.all()
        ],
        "discount_amount": str(invoice.discount_amount),
        "currency": invoice.currency,
        "issue_date": invoice.issue_date.isoformat(),
        "created_by_user_id": str(invoice.created_by_id) if invoice.created_by_id else None,
    }
    if invoice.client_id:
        payload["client"] = _serialize_client(invoice.client_id)
    return payload


def _serialize_payment(object_id):
    from apps.sales.models import Payment

    payment = Payment.objects.get(pk=object_id)
    return {
        "id": str(payment.id),
        "created_at": payment.paid_at.isoformat(),
        "invoice_id": str(payment.invoice_id),
        "amount": str(payment.amount),
        "method": payment.method,
        "reference": payment.reference,
        "created_by_user_id": str(payment.created_by_id) if payment.created_by_id else None,
    }


def _serialize_sale_transaction(object_id):
    from apps.sales.models import Sale

    sale = Sale.objects.select_related("client", "invoice").prefetch_related("lines").get(pk=object_id)
    lines_payload = []
    for line in sale.lines.all():
        movement = line.stock_movements.first()
        lines_payload.append({
            "product_id": str(line.product_id) if line.product_id else None,
            "description": line.description,
            "quantity": str(line.quantity),
            "unit_price_ht": str(line.unit_price_ht),
            "tva_rate": str(line.tva_rate),
            "stock_movement_id": str(movement.id) if movement else None,
        })

    payload = {
        "id": str(sale.id),
        "created_at": sale.created_at.isoformat(),
        "sale_date": sale.sale_date.isoformat(),
        "currency": sale.currency,
        "lines": lines_payload,
        "generate_invoice": sale.invoice_id is not None,
        "created_by_user_id": str(sale.created_by_id) if sale.created_by_id else None,
    }
    if sale.client_id:
        payload["client"] = _serialize_client(sale.client_id)
    if sale.invoice_id:
        payload["invoice_id"] = str(sale.invoice_id)
        payment = sale.invoice.payments.order_by("created_at").first()
        if payment is not None:
            payload["payment"] = {
                "id": str(payment.id),
                "created_at": payment.paid_at.isoformat(),
                "amount": str(payment.amount),
                "method": payment.method,
                "reference": payment.reference,
            }
    return payload


def _serialize_category(object_id):
    from apps.catalog.models import Category

    category = Category.objects.get(pk=object_id)
    return {
        "id": str(category.id), "name": category.name,
        "parent_id": str(category.parent_id) if category.parent_id else None,
    }


def _serialize_unit(object_id):
    from apps.catalog.models import Unit

    unit = Unit.objects.get(pk=object_id)
    return {"id": str(unit.id), "name": unit.name, "symbol": unit.symbol}


def _serialize_product(object_id):
    from apps.catalog.models import Product

    product = Product.objects.get(pk=object_id)
    return {
        "id": str(product.id),
        "category_id": str(product.category_id) if product.category_id else None,
        "name": product.name,
        "sku": product.sku,
        "barcode": product.barcode,
        "unit_id": str(product.unit_id),
        "default_sale_price": str(product.default_sale_price),
        "tva_rate": str(product.tva_rate),
        "low_stock_threshold_default": product.low_stock_threshold_default,
        "is_active": product.is_active,
    }


PUSH_ENDPOINTS = {
    OutboxEntry.CLIENT: ("push/clients/", _serialize_client),
    OutboxEntry.STOCK_MOVEMENT: ("push/stock-movements/", _serialize_stock_movement),
    OutboxEntry.INVOICE: ("push/invoices/", _serialize_invoice),
    OutboxEntry.PAYMENT: ("push/payments/", _serialize_payment),
    OutboxEntry.SALE_TRANSACTION: ("push/sale-transactions/", _serialize_sale_transaction),
    # Catégories/unités avant produits : Product.unit_id est une FK requise
    # côté serveur (voir ProductPushSerializer) — si le produit est poussé
    # avant son unité lors du même cycle, le push échoue inutilement.
    OutboxEntry.CATEGORY: ("push/catalog/categories/", _serialize_category),
    OutboxEntry.UNIT: ("push/catalog/units/", _serialize_unit),
    OutboxEntry.PRODUCT: ("push/catalog/products/", _serialize_product),
}


MAX_ATTEMPTS = 20  # au-delà, une entrée en erreur cesse d'être retentée automatiquement


def run_push_cycle(client=None):
    client = client or get_active_client()
    summary = {}

    for kind, (path, serialize_fn) in PUSH_ENDPOINTS.items():
        # Les entrées ERROR sont retentées (pas seulement PENDING) : une
        # erreur peut être transitoire (ex: produit pas encore présent
        # localement au moment du premier essai, mais arrivé depuis via un
        # pull plus récent) — sans ça, une entrée en erreur ne se
        # synchronise plus jamais, même une fois la cause corrigée.
        pending = list(
            OutboxEntry.objects.filter(kind=kind)
            .filter(
                models.Q(status=OutboxEntry.PENDING)
                | models.Q(status=OutboxEntry.ERROR, attempts__lt=MAX_ATTEMPTS)
            )
            .order_by("created_at")[:BATCH_SIZE]
        )
        if not pending:
            summary[kind] = {"sent": 0, "error": 0}
            continue

        items = []
        entries_by_id = {}
        for entry in pending:
            try:
                item = serialize_fn(entry.object_id)
            except Exception as exc:  # noqa: BLE001 — enregistrement disparu/corrompu localement
                entry.status = OutboxEntry.ERROR
                entry.attempts += 1
                entry.last_error = str(exc)
                entry.save(update_fields=["status", "attempts", "last_error", "updated_at"])
                continue
            items.append(item)
            entries_by_id[item["id"]] = entry

        if not items:
            summary[kind] = {"sent": 0, "error": len(pending)}
            continue

        idempotency_key = str(uuid.uuid4())
        try:
            response = client.post(path, {"items": items}, headers={"Idempotency-Key": idempotency_key})
        except Exception as exc:  # noqa: BLE001 — réseau/serveur en panne : rien n'est marqué, réessai au prochain cycle
            summary[kind] = {"sent": 0, "error": 0, "detail": str(exc)}
            continue

        sent = errored = 0
        for result in response.get("results", []):
            entry = entries_by_id.get(result.get("id"))
            if entry is None:
                continue
            if result.get("status") in ("created", "duplicate"):
                entry.status = OutboxEntry.SENT
                entry.sent_at = timezone.now()
                entry.save(update_fields=["status", "sent_at", "updated_at"])
                sent += 1
            else:
                entry.status = OutboxEntry.ERROR
                entry.attempts += 1
                entry.last_error = str(result.get("detail", ""))
                entry.save(update_fields=["status", "attempts", "last_error", "updated_at"])
                errored += 1
        summary[kind] = {"sent": sent, "error": errored}

    return summary
