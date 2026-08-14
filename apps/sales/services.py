from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.stock import services as stock_services
from apps.stock.models import StockMovement

from .models import Client, Invoice, InvoiceLine, Payment, Sale, SaleLine


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


def get_or_create_client(*, boutique, id=None, name, phone="", email="", address="", nif=""):
    """Récupère un client déjà synchronisé par `id`, ou le crée sinon —
    permet à un client créé hors-ligne d'être poussé avec son UUID
    d'origine sans jamais créer de doublon au rejeu."""

    if id is not None:
        existing = Client.objects.filter(pk=id, boutique=boutique).first()
        if existing is not None:
            return existing

    kwargs = dict(boutique=boutique, name=name, phone=phone, email=email, address=address, nif=nif)
    if id is not None:
        kwargs["id"] = id
    return Client.objects.create(**kwargs)


@transaction.atomic
def build_invoice(*, boutique, client, type, created_by, lines_data, issue_date=None,
                   discount_amount=Decimal("0"), currency=None, note="", pdf_format=None,
                   id=None, created_at=None):
    """Crée une facture/devis en BROUILLON avec ses lignes. `lines_data` :
    liste de dicts {product, description, quantity, unit_price_ht, tva_rate,
    discount_amount}. `discount_amount` est la remise globale de la
    facture (montant fixe FCFA). `currency` : devise de ce document
    précis — par défaut celle de la boutique, mais peut être différente
    (ex: devis facturé en USD pour un client étranger, boutique en XOF).
    `id`/`created_at` : rejeu idempotent d'une facture/devis créé
    hors-ligne — si `id` existe déjà, la facture est renvoyée telle
    quelle, sans recréer ses lignes. Ne touche pas au stock — voir
    validate_invoice()."""

    if id is not None:
        existing = Invoice.objects.filter(pk=id).first()
        if existing is not None:
            return existing

    issue_date = issue_date or timezone.localdate()
    number = (
        Invoice.generate_commande_number(boutique, issue_date)
        if type == Invoice.COMMANDE
        else Invoice.generate_number(boutique, issue_date)
    )
    invoice_kwargs = dict(
        boutique=boutique,
        client=client,
        type=type,
        number=number,
        issue_date=issue_date,
        currency=currency or boutique.devise,
        discount_amount=discount_amount,
        note=note,
        created_by=created_by,
    )
    if pdf_format:
        invoice_kwargs["pdf_format"] = pdf_format
    if id is not None:
        invoice_kwargs["id"] = id
    if created_at is not None:
        invoice_kwargs["created_at"] = created_at
    invoice = Invoice.objects.create(**invoice_kwargs)

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
def update_invoice(invoice, *, client, lines_data, discount_amount=Decimal("0"), currency=None,
                    note=None, pdf_format=None):
    """Modifie un devis encore éditable (voir devis_update — pas encore
    converti en facture) : les lignes existantes sont supprimées puis
    recréées à partir de lines_data, plus simple et plus sûr qu'un diff
    ligne à ligne pour ce cas d'usage (le formulaire est resoumis en
    entier à chaque édition, pas d'édition partielle en direct)."""

    invoice.lines.all().delete()

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

    invoice.client = client
    invoice.discount_amount = discount_amount
    invoice.subtotal_ht = subtotal_ht
    invoice.total_tva = total_tva
    invoice.total_ttc = total_ttc
    if currency:
        invoice.currency = currency
    if note is not None:
        invoice.note = note
    if pdf_format:
        invoice.pdf_format = pdf_format
    invoice.save(update_fields=[
        "client", "discount_amount", "subtotal_ht", "total_tva", "total_ttc",
        "currency", "note", "pdf_format", "updated_at",
    ])
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
def convert_devis_to_invoice(devis, created_by=None):
    """Transforme un devis validé par le client en facture réelle : copie
    ses lignes dans une nouvelle Invoice(type=FACTURE), déduit le stock des
    lignes reliées à un produit (le devis lui-même n'y touche jamais — voir
    build_invoice) et marque le devis CONVERTIE. Idempotent : renvoie la
    facture déjà générée si l'opération a déjà eu lieu."""

    if devis.type != Invoice.DEVIS:
        raise ValueError("Seul un devis peut être converti en facture.")

    existing = devis.conversions.filter(type=Invoice.FACTURE).first()
    if existing:
        return existing

    issue_date = timezone.localdate()
    invoice = Invoice.objects.create(
        boutique=devis.boutique,
        client=devis.client,
        type=Invoice.FACTURE,
        status=Invoice.VALIDEE,
        number=Invoice.generate_number(devis.boutique, issue_date),
        issue_date=issue_date,
        currency=devis.currency,
        subtotal_ht=devis.subtotal_ht,
        total_tva=devis.total_tva,
        total_ttc=devis.total_ttc,
        discount_amount=devis.discount_amount,
        converted_from=devis,
        created_by=created_by,
    )

    for line in devis.lines.all():
        InvoiceLine.objects.create(
            invoice=invoice,
            product=line.product,
            description=line.description,
            quantity=line.quantity,
            unit_price_ht=line.unit_price_ht,
            tva_rate=line.tva_rate,
            discount_amount=line.discount_amount,
            line_total_ht=line.line_total_ht,
            line_total_ttc=line.line_total_ttc,
            position=line.position,
        )
        if line.product is not None:
            stock_services.apply_movement(
                boutique=devis.boutique,
                product=line.product,
                type=StockMovement.VENTE,
                quantity=-line.quantity,
                reference_invoice=invoice,
                created_by=created_by,
                reason=f"Facture {invoice.number} (devis {devis.number})",
            )

    devis.status = Invoice.CONVERTIE
    devis.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def convert_commande_to_invoice(commande, created_by=None):
    """Transforme une commande validée en facture réelle : copie ses
    lignes dans une nouvelle Invoice(type=FACTURE), déduit le stock des
    lignes reliées à un produit (comme convert_devis_to_invoice) et marque
    la commande CONVERTIE. Contrairement au devis, une commande peut déjà
    porter un acompte au moment de la conversion — ces paiements sont
    reportés sur la facture générée (pas dupliqués) pour que le "reste à
    payer" soit correct dès l'affichage. Idempotente : renvoie la facture
    déjà générée si l'opération a déjà eu lieu."""

    if commande.type != Invoice.COMMANDE:
        raise ValueError("Seule une commande peut être convertie en facture.")

    existing = commande.conversions.filter(type=Invoice.FACTURE).first()
    if existing:
        return existing

    issue_date = timezone.localdate()
    invoice = Invoice.objects.create(
        boutique=commande.boutique,
        client=commande.client,
        type=Invoice.FACTURE,
        status=Invoice.VALIDEE,
        number=Invoice.generate_number(commande.boutique, issue_date),
        issue_date=issue_date,
        currency=commande.currency,
        subtotal_ht=commande.subtotal_ht,
        total_tva=commande.total_tva,
        total_ttc=commande.total_ttc,
        discount_amount=commande.discount_amount,
        converted_from=commande,
        created_by=created_by,
    )

    for line in commande.lines.all():
        InvoiceLine.objects.create(
            invoice=invoice,
            product=line.product,
            description=line.description,
            quantity=line.quantity,
            unit_price_ht=line.unit_price_ht,
            tva_rate=line.tva_rate,
            discount_amount=line.discount_amount,
            line_total_ht=line.line_total_ht,
            line_total_ttc=line.line_total_ttc,
            position=line.position,
        )
        if line.product is not None:
            stock_services.apply_movement(
                boutique=commande.boutique,
                product=line.product,
                type=StockMovement.VENTE,
                quantity=-line.quantity,
                reference_invoice=invoice,
                created_by=created_by,
                reason=f"Facture {invoice.number} (commande {commande.number})",
            )

    commande.payments.update(invoice=invoice)
    _apply_payment_status(invoice)

    commande.status = Invoice.CONVERTIE
    commande.save(update_fields=["status", "updated_at"])
    return invoice


def _apply_payment_status(invoice):
    """Recalcule et sauvegarde le statut PAYEE/PARTIELLEMENT_PAYEE à partir
    du total déjà payé — factorisé pour être réutilisé aussi bien après un
    nouveau paiement (record_payment) qu'après un report de paiements
    existants sur une nouvelle facture (convert_commande_to_invoice)."""

    total_paid = invoice.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if total_paid >= invoice.total_ttc:
        invoice.status = Invoice.PAYEE
    elif total_paid > 0:
        invoice.status = Invoice.PARTIELLEMENT_PAYEE
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def record_payment(invoice, *, amount, method, reference="", created_by=None, id=None, created_at=None):
    """`id`/`created_at` : rejeu idempotent d'un paiement enregistré
    hors-ligne — si `id` existe déjà, ne recrée rien et ne recalcule pas
    le statut une seconde fois."""

    if id is not None and Payment.objects.filter(pk=id).exists():
        return invoice

    payment_kwargs = dict(
        invoice=invoice,
        boutique=invoice.boutique,
        amount=amount,
        method=method,
        reference=reference,
        created_by=created_by,
    )
    if id is not None:
        payment_kwargs["id"] = id
    if created_at is not None:
        payment_kwargs["paid_at"] = created_at
    Payment.objects.create(**payment_kwargs)

    return _apply_payment_status(invoice)


@transaction.atomic
def build_sale(*, boutique, client, created_by, lines_data, sale_date=None, currency=None,
                id=None, created_at=None):
    """Crée une vente en BROUILLON avec ses lignes (le panier). `currency` :
    devise de cette vente précise — par défaut celle de la boutique, mais
    peut être différente au cas par cas. `id`/`created_at` : rejeu
    idempotent d'une vente créée hors-ligne — si `id` existe déjà, la
    vente est renvoyée telle quelle, sans recréer ses lignes. Ne touche
    pas au stock — c'est confirm_sale() qui le fait, au moment où la
    vente devient réelle."""

    if id is not None:
        existing = Sale.objects.filter(pk=id).first()
        if existing is not None:
            return existing

    sale_date = sale_date or timezone.localdate()
    sale_kwargs = dict(
        boutique=boutique,
        client=client,
        number=Sale.generate_number(boutique, sale_date),
        sale_date=sale_date,
        currency=currency or boutique.devise,
        created_by=created_by,
    )
    if id is not None:
        sale_kwargs["id"] = id
    if created_at is not None:
        sale_kwargs["created_at"] = created_at
    sale = Sale.objects.create(**sale_kwargs)

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
def confirm_sale(sale, created_by=None, movement_ids=None, created_at=None,
                  source=StockMovement.SOURCE_ONLINE):
    """Confirme la vente et déduit le stock des lignes reliées à un
    produit, dans la même transaction. C'est le seul moment où une vente
    impacte le stock — idempotent : ne fait rien si déjà confirmée.
    `movement_ids` : liste optionnelle alignée sur `sale.lines` (position),
    un UUID par ligne reliée à un produit — permet de rejouer les
    mouvements de stock d'une vente confirmée hors-ligne avec leurs UUID
    d'origine (voir apply_movement). `source` : traçabilité du mouvement
    (ONLINE/OFFLINE) — à passer explicitement SOURCE_OFFLINE lors d'un
    rejeu depuis le bundle de push (voir apps.sync.views)."""

    if sale.status != Sale.BROUILLON:
        return sale

    for index, line in enumerate(sale.lines.select_related("product")):
        if line.product is None:
            continue
        stock_services.apply_movement(
            boutique=sale.boutique,
            product=line.product,
            type=StockMovement.VENTE,
            quantity=-line.quantity,
            reference_sale=sale,
            sale_line=line,
            created_by=created_by,
            reason=f"Vente {sale.number}",
            movement_id=movement_ids[index] if movement_ids else None,
            created_at=created_at,
            source=source,
        )

    sale.status = Sale.CONFIRMEE
    sale.save(update_fields=["status", "updated_at"])
    return sale


@transaction.atomic
def generate_invoice_from_sale(sale, created_by=None, id=None, created_at=None):
    """Génère la facture correspondant à une vente déjà confirmée, en
    copiant ses lignes. Le stock a déjà été déduit à la confirmation de la
    vente : la facture est donc créée directement en statut VALIDEE, sans
    nouveau mouvement de stock. Idempotent : renvoie la facture existante
    si elle a déjà été générée. `id`/`created_at` : rejeu idempotent d'une
    facture générée hors-ligne."""

    if sale.invoice_id:
        return sale.invoice

    if sale.status != Sale.CONFIRMEE:
        raise ValueError("Seule une vente confirmée peut être facturée.")

    invoice_kwargs = dict(
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
    if id is not None:
        invoice_kwargs["id"] = id
    if created_at is not None:
        invoice_kwargs["created_at"] = created_at
    invoice = Invoice.objects.create(**invoice_kwargs)

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
