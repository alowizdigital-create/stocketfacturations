"""Génération du ticket de facture/devis au format 80 mm, dimensionné pour
une imprimante thermique de caisse (pas de mise en page A4 : la hauteur de
la page PDF est calculée pour correspondre exactement au contenu, comme un
ticket de caisse)."""

import io
import logging

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas

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
        n += len(payments)  # une ligne par paiement
    if payment_methods:
        separator_count += 1
        n += 1 + len(payment_methods) * 2  # titre + 2 lignes par moyen de paiement
    n += 3  # remerciement + entreprise
    height = TOP_BOTTOM_PADDING + n * LINE_HEIGHT + separator_count * SEPARATOR_HEIGHT + TITLE_HEIGHT
    if logo_sized is not None:
        height += logo_sized[2] + 1.5 * mm
    return height


def render_invoice_pdf(invoice):
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

    title = "FACTURE" if invoice.type == invoice.FACTURE else "DEVIS"

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
