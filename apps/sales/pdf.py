"""Génération du PDF de facture/devis/commande, en deux formats au choix
(voir Invoice.pdf_format) : un ticket 80 mm dimensionné pour une imprimante
thermique de caisse (hauteur de page calculée pour correspondre exactement
au contenu), ou une page A4 classique (mise en page tabulaire via reportlab
Platypus, avec saut de page automatique pour les documents à beaucoup de
lignes)."""

import io
import logging

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)

PAGE_WIDTH = 80 * mm
MARGIN = 4 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
LOGO_MAX_HEIGHT = 16 * mm

FONT_NORMAL = ("Helvetica", 8)
FONT_BOLD = ("Helvetica-Bold", 8)
FONT_SMALL = ("Helvetica", 7)
FONT_TITLE = ("Helvetica-Bold", 12)

LINE_HEIGHT = 3.6 * mm
TITLE_HEIGHT = 5.5 * mm
SEPARATOR_HEIGHT = 3 * mm
TOP_BOTTOM_PADDING = 6 * mm


def _fmt(value):
    return f"{value:,.0f}".replace(",", " ")


def _logo_size(logo_field):
    """Dimensions à l'échelle du logo (largeur, hauteur), bornées par la
    largeur du ticket et LOGO_MAX_HEIGHT, en conservant les proportions.
    Renvoie None si pas de logo ou fichier illisible (ne doit jamais faire
    échouer la génération du PDF pour une facture par ailleurs valide)."""
    if not logo_field:
        return None
    try:
        image = ImageReader(logo_field.path)
        img_w, img_h = image.getSize()
    except Exception:
        logger.warning("Logo illisible pour le PDF (%s), ignoré.", logo_field)
        return None
    scale = min(CONTENT_WIDTH / img_w, LOGO_MAX_HEIGHT / img_h)
    return image, img_w * scale, img_h * scale


def _truncate(c, text, font, max_width):
    """Coupe le texte avec '…' s'il dépasse la largeur disponible, plutôt
    que de le laisser déborder ou se superposer à la colonne des prix."""
    c.setFont(*font)
    if c.stringWidth(text, *font) <= max_width:
        return text
    while text and c.stringWidth(text + "…", *font) > max_width:
        text = text[:-1]
    return text + "…"


class _ReceiptWriter:
    """Petit wrapper autour du canvas bas niveau reportlab : dessine ligne
    par ligne du haut vers le bas, ce qui donne un contrôle précis sur la
    mise en page d'un ticket (impossible à obtenir aussi simplement avec
    les Flowables Platypus, pensés pour des pages A4)."""

    def __init__(self, canvas, height):
        self.c = canvas
        self.y = height - MARGIN - 2 * mm

    def text(self, value, font=FONT_NORMAL, align="left"):
        self.c.setFont(*font)
        if align == "center":
            self.c.drawCentredString(PAGE_WIDTH / 2, self.y, value)
        elif align == "right":
            self.c.drawRightString(PAGE_WIDTH - MARGIN, self.y, value)
        else:
            self.c.drawString(MARGIN, self.y, value)
        self.y -= LINE_HEIGHT

    def two_columns(self, left, right, font=FONT_NORMAL):
        self.c.setFont(*font)
        left = _truncate(self.c, left, font, CONTENT_WIDTH * 0.6)
        self.c.drawString(MARGIN, self.y, left)
        self.c.drawRightString(PAGE_WIDTH - MARGIN, self.y, right)
        self.y -= LINE_HEIGHT

    def separator(self):
        self.y -= 0.5 * mm
        self.c.setLineWidth(0.6)
        self.c.line(MARGIN, self.y, PAGE_WIDTH - MARGIN, self.y)
        self.y -= SEPARATOR_HEIGHT - 0.5 * mm

    def blank(self, height=LINE_HEIGHT / 2):
        self.y -= height

    def logo(self, logo_sized):
        image, draw_w, draw_h = logo_sized
        self.y -= draw_h
        self.c.drawImage(
            image, (PAGE_WIDTH - draw_w) / 2, self.y, width=draw_w, height=draw_h,
            mask="auto", preserveAspectRatio=True,
        )
        self.y -= 1.5 * mm


def _estimate_height(invoice, lines, payments, logo_sized, has_global_discount, client_phone, payment_methods):
    n = 0
    separator_count = 4  # header, infos client, après lignes, avant pied de page
    n += 3  # nom boutique + adresse/téléphone (jusqu'à 2 lignes)
    n += 3  # numéro, date, client
    if client_phone:
        n += 1  # téléphone du destinataire
    n += len(lines) * 2  # description + qté×PU = total
    n += 3  # sous-total / TVA / total
    if has_global_discount:
        n += 1  # ligne remise globale
    if payments:
        separator_count += 1
        n += len(payments) + 2  # une ligne par paiement + payé + reste à payer
    if payment_methods:
        separator_count += 1
        n += 1 + len(payment_methods) * 2  # titre + 2 lignes par moyen de paiement
    n += 3  # remerciement + entreprise
    height = TOP_BOTTOM_PADDING + n * LINE_HEIGHT + separator_count * SEPARATOR_HEIGHT + TITLE_HEIGHT
    if logo_sized is not None:
        height += logo_sized[2] + 1.5 * mm
    return height


def _document_title(invoice):
    if invoice.type == invoice.FACTURE:
        return "FACTURE"
    elif invoice.type == invoice.COMMANDE:
        return "COMMANDE"
    return "DEVIS"


def render_invoice_pdf(invoice):
    """Point d'entrée unique pour générer le PDF d'une facture/devis/
    commande — choisit le rendu (ticket 80mm ou page A4) selon
    `invoice.pdf_format`, transparent pour les deux vues appelantes
    (invoice_pdf, invoice_public_pdf)."""
    if invoice.pdf_format == invoice.PDF_FORMAT_A4:
        return _render_invoice_pdf_a4(invoice)
    return _render_invoice_pdf_80mm(invoice)


def _render_invoice_pdf_80mm(invoice):
    """Rendu du ticket 80 mm — utilisé aussi bien pour l'impression directe
    (imprimante thermique) que pour le lien de partage WhatsApp."""

    lines = list(invoice.lines.all())
    payments = list(invoice.payments.all())
    logo_field = invoice.boutique.compte.logo
    logo_sized = _logo_size(logo_field)
    has_global_discount = bool(invoice.discount_amount)
    client_phone = invoice.client.phone if invoice.client else ""
    payment_methods = invoice.boutique.payment_methods

    height = _estimate_height(
        invoice, lines, payments, logo_sized, has_global_discount, client_phone, payment_methods
    )

    buffer = io.BytesIO()
    c = pdfcanvas.Canvas(buffer, pagesize=(PAGE_WIDTH, height))
    w = _ReceiptWriter(c, height)

    title = _document_title(invoice)

    if logo_sized is not None:
        w.logo(logo_sized)
    w.text(invoice.boutique.name, font=FONT_TITLE, align="center")
    if invoice.boutique.address:
        w.text(invoice.boutique.address, font=FONT_SMALL, align="center")
    if invoice.boutique.phone:
        w.text(f"Tél: {invoice.boutique.phone}", font=FONT_SMALL, align="center")
    w.separator()

    w.text(f"{title} {invoice.number}", font=FONT_BOLD)
    w.text(f"Date: {invoice.issue_date:%d/%m/%Y}")
    w.text(f"Client: {invoice.client.name if invoice.client else 'Client de passage'}")
    if client_phone:
        w.text(f"Tél: {client_phone}")
    w.separator()

    for line in lines:
        description = _truncate(c, line.description, FONT_NORMAL, CONTENT_WIDTH)
        w.text(description)
        detail = f"{float(line.quantity):g} x {_fmt(line.unit_price_ht)}"
        if line.discount_amount:
            detail += f" (-{_fmt(line.discount_amount)})"
        w.two_columns(detail, _fmt(line.line_total_ttc))
    w.separator()

    w.two_columns("Sous-total HT", f"{_fmt(invoice.subtotal_ht)} {invoice.currency}")
    w.two_columns("TVA", f"{_fmt(invoice.total_tva)} {invoice.currency}")
    if has_global_discount:
        w.two_columns("Remise globale", f"-{_fmt(invoice.discount_amount)} {invoice.currency}")
    w.two_columns("TOTAL TTC", f"{_fmt(invoice.total_ttc)} {invoice.currency}", font=FONT_BOLD)

    if payments:
        w.separator()
        for payment in payments:
            w.two_columns(payment.get_method_display(), f"{_fmt(payment.amount)} {invoice.currency}")
        w.two_columns("Payé", f"{_fmt(invoice.amount_paid)} {invoice.currency}")
        w.two_columns("Reste à payer", f"{_fmt(invoice.balance_due)} {invoice.currency}", font=FONT_BOLD)

    if payment_methods:
        w.separator()
        w.text("Paiement Mobile Money", font=FONT_BOLD)
        for method in payment_methods:
            label = method["label"]
            if method["account_name"]:
                label += f" ({method['account_name']})"
            w.two_columns(label, method["number"])

    w.separator()
    w.text("Merci pour la confiance!", align="center")
    w.text(invoice.boutique.name, font=FONT_SMALL, align="center")

    c.showPage()
    c.save()
    return buffer.getvalue()


A4_MARGIN = 18 * mm

_styles = getSampleStyleSheet()
_STYLE_NORMAL = ParagraphStyle("InvoiceNormal", parent=_styles["Normal"], fontSize=9, leading=12)
_STYLE_SMALL = ParagraphStyle(
    "InvoiceSmall", parent=_styles["Normal"], fontSize=8, leading=10, textColor=colors.grey
)
_STYLE_TITLE = ParagraphStyle(
    "InvoiceTitle", parent=_styles["Normal"], fontSize=18, leading=22, fontName="Helvetica-Bold"
)
_STYLE_HEADER_CELL = ParagraphStyle(
    "InvoiceHeaderCell", parent=_styles["Normal"], fontSize=9, leading=11,
    fontName="Helvetica-Bold", textColor=colors.white,
)
_STYLE_BOLD = ParagraphStyle("InvoiceBold", parent=_styles["Normal"], fontSize=9, leading=12, fontName="Helvetica-Bold")


def _render_invoice_pdf_a4(invoice):
    """Rendu A4 classique (reportlab Platypus) — même contenu que le ticket
    80mm mais en page complète, avec un vrai tableau de lignes (saut de
    page automatique pour les documents à beaucoup de lignes, impossible à
    obtenir avec le canvas bas niveau du rendu 80mm)."""

    lines = list(invoice.lines.all())
    payments = list(invoice.payments.all())
    logo_field = invoice.boutique.compte.logo
    has_global_discount = bool(invoice.discount_amount)
    client_phone = invoice.client.phone if invoice.client else ""
    payment_methods = invoice.boutique.payment_methods
    title = _document_title(invoice)

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=A4_MARGIN, bottomMargin=A4_MARGIN, leftMargin=A4_MARGIN, rightMargin=A4_MARGIN,
        title=f"{title} {invoice.number}",
    )
    story = []

    # --- En-tête : logo/coordonnées boutique à gauche, titre/numéro/date à droite
    left_cell = []
    logo_sized = _logo_size(logo_field)
    if logo_sized is not None:
        image, draw_w, draw_h = logo_sized
        left_cell.append(Image(logo_field.path, width=draw_w, height=draw_h))
        left_cell.append(Spacer(1, 3 * mm))
    left_cell.append(Paragraph(f"<b>{invoice.boutique.name}</b>", _STYLE_NORMAL))
    if invoice.boutique.address:
        left_cell.append(Paragraph(invoice.boutique.address, _STYLE_SMALL))
    if invoice.boutique.phone:
        left_cell.append(Paragraph(f"Tél: {invoice.boutique.phone}", _STYLE_SMALL))

    right_cell = [
        Paragraph(title, _STYLE_TITLE),
        Paragraph(invoice.number, _STYLE_NORMAL),
        Paragraph(f"Date: {invoice.issue_date:%d/%m/%Y}", _STYLE_SMALL),
    ]

    header_table = Table([[left_cell, right_cell]], colWidths=[100 * mm, None])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 6 * mm))

    # --- Client
    client_name = invoice.client.name if invoice.client else "Client de passage"
    client_text = f"<b>Client :</b> {client_name}"
    if client_phone:
        client_text += f" — Tél: {client_phone}"
    story.append(Paragraph(client_text, _STYLE_NORMAL))
    story.append(Spacer(1, 6 * mm))

    # --- Tableau des lignes
    header_row = [
        Paragraph("Description", _STYLE_HEADER_CELL),
        Paragraph("Qté", _STYLE_HEADER_CELL),
        Paragraph("PU HT", _STYLE_HEADER_CELL),
        Paragraph("Remise", _STYLE_HEADER_CELL),
        Paragraph("Total TTC", _STYLE_HEADER_CELL),
    ]
    rows = [header_row]
    for line in lines:
        rows.append([
            Paragraph(line.description, _STYLE_NORMAL),
            Paragraph(f"{float(line.quantity):g}", _STYLE_NORMAL),
            Paragraph(_fmt(line.unit_price_ht), _STYLE_NORMAL),
            Paragraph(_fmt(line.discount_amount) if line.discount_amount else "—", _STYLE_NORMAL),
            Paragraph(_fmt(line.line_total_ttc), _STYLE_NORMAL),
        ])

    lines_table = Table(rows, colWidths=[None, 18 * mm, 28 * mm, 25 * mm, 30 * mm], repeatRows=1)
    lines_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6f4a8e")),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f7fa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(lines_table)
    story.append(Spacer(1, 6 * mm))

    # --- Totaux (mini tableau aligné à droite)
    totals_rows = [
        ("Sous-total HT", f"{_fmt(invoice.subtotal_ht)} {invoice.currency}"),
        ("TVA", f"{_fmt(invoice.total_tva)} {invoice.currency}"),
    ]
    if has_global_discount:
        totals_rows.append(("Remise globale", f"-{_fmt(invoice.discount_amount)} {invoice.currency}"))
    totals_rows.append(("TOTAL TTC", f"{_fmt(invoice.total_ttc)} {invoice.currency}"))

    totals_table = Table(
        [
            [Paragraph(label, _STYLE_NORMAL), Paragraph(value, _STYLE_BOLD if label == "TOTAL TTC" else _STYLE_NORMAL)]
            for label, value in totals_rows
        ],
        colWidths=[40 * mm, 40 * mm], hAlign="RIGHT",
    )
    totals_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(totals_table)

    # --- Paiements
    if payments:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("<b>Paiements</b>", _STYLE_NORMAL))
        payment_rows = [
            (payment.get_method_display(), f"{_fmt(payment.amount)} {invoice.currency}")
            for payment in payments
        ]
        payment_rows.append(("Payé", f"{_fmt(invoice.amount_paid)} {invoice.currency}"))
        payment_rows.append(("Reste à payer", f"{_fmt(invoice.balance_due)} {invoice.currency}"))
        payments_table = Table(
            [[Paragraph(label, _STYLE_NORMAL), Paragraph(value, _STYLE_NORMAL)] for label, value in payment_rows],
            colWidths=[40 * mm, 40 * mm], hAlign="RIGHT",
        )
        payments_table.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT")]))
        story.append(payments_table)

    # --- Moyens de paiement mobile money
    if payment_methods:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph("<b>Paiement Mobile Money</b>", _STYLE_NORMAL))
        for method in payment_methods:
            label = method["label"]
            if method["account_name"]:
                label += f" ({method['account_name']})"
            story.append(Paragraph(f"{label} : {method['number']}", _STYLE_NORMAL))

    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph("Merci pour la confiance !", _STYLE_SMALL))

    doc.build(story)
    return buffer.getvalue()
