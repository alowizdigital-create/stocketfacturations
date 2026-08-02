import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet


def render_invoice_pdf(invoice):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, topMargin=20 * mm, bottomMargin=20 * mm)
    styles = getSampleStyleSheet()
    story = []

    title = "FACTURE" if invoice.type == invoice.FACTURE else "DEVIS"
    story.append(Paragraph(f"{title} {invoice.number}", styles["Title"]))
    story.append(Paragraph(invoice.boutique.name, styles["Normal"]))
    if invoice.boutique.address:
        story.append(Paragraph(invoice.boutique.address, styles["Normal"]))
    story.append(Spacer(1, 8 * mm))

    story.append(Paragraph(f"Date : {invoice.issue_date:%d/%m/%Y}", styles["Normal"]))
    if invoice.client:
        story.append(Paragraph(f"Client : {invoice.client.name}", styles["Normal"]))
        if invoice.client.phone:
            story.append(Paragraph(f"Téléphone : {invoice.client.phone}", styles["Normal"]))
    story.append(Spacer(1, 6 * mm))

    data = [["Description", "Qté", "PU HT", "TVA %", "Total TTC"]]
    for line in invoice.lines.all():
        data.append(
            [
                line.description,
                str(line.quantity),
                f"{line.unit_price_ht:,.0f}".replace(",", " "),
                f"{line.tva_rate}",
                f"{line.line_total_ttc:,.0f}".replace(",", " "),
            ]
        )

    table = Table(data, colWidths=[70 * mm, 20 * mm, 30 * mm, 20 * mm, 30 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2d3748")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 8 * mm))

    totals = [
        ["Sous-total HT", f"{invoice.subtotal_ht:,.0f} {invoice.currency}".replace(",", " ")],
        ["TVA", f"{invoice.total_tva:,.0f} {invoice.currency}".replace(",", " ")],
        ["Total TTC", f"{invoice.total_ttc:,.0f} {invoice.currency}".replace(",", " ")],
    ]
    totals_table = Table(totals, colWidths=[40 * mm, 40 * mm], hAlign="RIGHT")
    totals_table.setStyle(
        TableStyle(
            [
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ]
        )
    )
    story.append(totals_table)

    doc.build(story)
    return buffer.getvalue()
