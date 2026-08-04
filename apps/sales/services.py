from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.stock import services as stock_services
from apps.stock.models import StockMovement

from .models import Invoice, InvoiceLine, Payment, Sale, SaleLine


def _line_totals(quantity, unit_price_ht, tva_rate, discount_amount=Decimal("0")):
    gross_ht = quantity * unit_price_ht
    line_total_ht = max(gross_ht - discount_amount, Decimal("0")).quantize(Decimal("1"))
    line_total_ttc = (line_total_ht * (Decimal("1") + tva_rate / Decimal("100"))).quantize(Decimal("1"))
    return line_total_ht, line_total_ttc


def _compute_totals(lines_data, discount_amount=Decimal("0")):
    """Calcule les lignes et les totaux. `discount_amount` est la remise
    globale de facture (montant fixe en FCFA), déduite directement du total
    TTC final. Chaque ligne peut aussi porter sa propre `discount_amount`
    (montant fixe déduit de son HT) dans `lines_data`."""

    subtotal_ht = Decimal("0")
    total_tva = Decimal("0")
    computed = []
    for line in lines_data:
        line_total_ht, line_total_ttc = _line_totals(
            line["quantity"],
            line["unit_price_ht"],
            line["tva_rate"],
            line.get("discount_amount", Decimal("0")),
        )
        computed.append((line, line_total_ht, line_total_ttc))
        subtotal_ht += line_total_ht
        total_tva += line_total_ttc - line_total_ht

    total_ttc = max(subtotal_ht + total_tva - discount_amount, Decimal("0"))
    return computed, subtotal_ht, total_tva, total_ttc


@transaction.atomic
def build_invoice(*, boutique, client, type, created_by, lines_data, issue_date=None,
                   discount_amount=Decimal("0")):
    """Crée une facture/devis en BROUILLON avec ses lignes. `lines_data` :
    liste de dicts {product, description, quantity, unit_price_ht, tva_rate,
    discount_amount}. `discount_amount` est la remise globale de la
    facture (montant fixe FCFA). Ne touche pas au stock — voir
    validate_invoice()."""

    issue_date = issue_date or timezone.localdate()
    invoice = Invoice.objects.create(
        boutique=boutique,
        client=client,
        type=type,
        number=Invoice.generate_number(boutique, issue_date),
        issue_date=issue_date,
        discount_amount=discount_amount,
        created_by=created_by,
    )

    computed, subtotal_ht, total_tva, total_ttc = _compute_totals(lines_data, discount_amount)
    for position, (line, line_total_ht, line_total_ttc) in enumerate(computed):
        InvoiceLine.objects.create(
            invoice=invoice,
            product=line.get("product"),
            description=line["description"],
            quantity=line["quantity"],
            unit_price_ht=line["unit_price_ht"],
            tva_rate=line["tva_rate"],
            discount_amount=line.get("discount_amount", Decimal("0")),
            line_total_ht=line_total_ht,
            line_total_ttc=line_total_ttc,
            position=position,
        )

    Invoice.objects.filter(pk=invoice.pk).update(
        subtotal_ht=subtotal_ht, total_tva=total_tva, total_ttc=total_ttc
    )
    invoice.refresh_from_db()
    return invoice


@transaction.atomic
def validate_invoice(invoice, created_by=None):
    """Valide un devis (simple changement de statut). N'impacte jamais le
    stock : un devis n'est pas une vente. Les factures, elles, sont créées
    directement en statut VALIDEE par generate_invoice_from_sale() — le
    stock est déjà déduit à ce moment-là, via la vente confirmée.
    Idempotent : ne fait rien si l'objet n'est plus au statut BROUILLON."""

    if invoice.status != Invoice.BROUILLON:
        return invoice

    invoice.status = Invoice.VALIDEE
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def record_payment(invoice, *, amount, method, reference="", created_by=None):
    Payment.objects.create(
        invoice=invoice,
        boutique=invoice.boutique,
        amount=amount,
        method=method,
        reference=reference,
        created_by=created_by,
    )

    total_paid = invoice.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if total_paid >= invoice.total_ttc:
        invoice.status = Invoice.PAYEE
    elif total_paid > 0:
        invoice.status = Invoice.PARTIELLEMENT_PAYEE
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def build_sale(*, boutique, client, created_by, lines_data, sale_date=None):
    """Crée une vente en BROUILLON avec ses lignes (le panier). Ne touche
    pas au stock — c'est confirm_sale() qui le fait, au moment où la vente
    devient réelle."""

    sale_date = sale_date or timezone.localdate()
    sale = Sale.objects.create(
        boutique=boutique,
        client=client,
        number=Sale.generate_number(boutique, sale_date),
        sale_date=sale_date,
        created_by=created_by,
    )

    computed, subtotal_ht, total_tva, total_ttc = _compute_totals(lines_data)
    for position, (line, line_total_ht, line_total_ttc) in enumerate(computed):
        SaleLine.objects.create(
            sale=sale,
            product=line.get("product"),
            description=line["description"],
            quantity=line["quantity"],
            unit_price_ht=line["unit_price_ht"],
            tva_rate=line["tva_rate"],
            line_total_ht=line_total_ht,
            line_total_ttc=line_total_ttc,
            position=position,
        )

    Sale.objects.filter(pk=sale.pk).update(
        subtotal_ht=subtotal_ht, total_tva=total_tva, total_ttc=total_ttc
    )
    sale.refresh_from_db()
    return sale


@transaction.atomic
def confirm_sale(sale, created_by=None):
    """Confirme la vente et déduit le stock des lignes reliées à un
    produit, dans la même transaction. C'est le seul moment où une vente
    impacte le stock — idempotent : ne fait rien si déjà confirmée."""

    if sale.status != Sale.BROUILLON:
        return sale

    for line in sale.lines.select_related("product"):
        if line.product is None:
            continue
        stock_services.apply_movement(
            boutique=sale.boutique,
            product=line.product,
            type=StockMovement.VENTE,
            quantity=-line.quantity,
            reference_sale=sale,
            created_by=created_by,
            reason=f"Vente {sale.number}",
        )

    sale.status = Sale.CONFIRMEE
    sale.save(update_fields=["status", "updated_at"])
    return sale


@transaction.atomic
def generate_invoice_from_sale(sale, created_by=None):
    """Génère la facture correspondant à une vente déjà confirmée, en
    copiant ses lignes. Le stock a déjà été déduit à la confirmation de la
    vente : la facture est donc créée directement en statut VALIDEE, sans
    nouveau mouvement de stock. Idempotent : renvoie la facture existante
    si elle a déjà été générée."""

    if sale.invoice_id:
        return sale.invoice

    if sale.status != Sale.CONFIRMEE:
        raise ValueError("Seule une vente confirmée peut être facturée.")

    invoice = Invoice.objects.create(
        boutique=sale.boutique,
        client=sale.client,
        type=Invoice.FACTURE,
        status=Invoice.VALIDEE,
        number=Invoice.generate_number(sale.boutique, sale.sale_date),
        issue_date=sale.sale_date,
        currency=sale.currency,
        subtotal_ht=sale.subtotal_ht,
        total_tva=sale.total_tva,
        total_ttc=sale.total_ttc,
        created_by=created_by,
    )

    for line in sale.lines.all():
        InvoiceLine.objects.create(
            invoice=invoice,
            product=line.product,
            description=line.description,
            quantity=line.quantity,
            unit_price_ht=line.unit_price_ht,
            tva_rate=line.tva_rate,
            line_total_ht=line.line_total_ht,
            line_total_ttc=line.line_total_ttc,
            position=line.position,
        )

    Sale.objects.filter(pk=sale.pk).update(invoice=invoice)
    sale.invoice = invoice
    return invoice
