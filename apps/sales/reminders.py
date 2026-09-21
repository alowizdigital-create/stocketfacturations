"""Relances de paiement : quelles factures sont impayées, qui doit quoi, et
le texte WhatsApp à envoyer (une facture, ou le relevé d'un client).

« Impayée » = facture (pas devis/commande) validée ou partiellement payée
dont il reste quelque chose à régler, rattachée à un client — sans client,
personne à relancer."""

from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Invoice, PaymentReminder

MAX_STATEMENT_LINES = 8

_ZERO = Value(Decimal("0"), output_field=DecimalField(max_digits=14, decimal_places=0))
_MONEY = DecimalField(max_digits=14, decimal_places=0)


def unpaid_invoices(boutique, client=None):
    """Factures impayées de la boutique (ou d'un client), avec `paid` et `due`
    (reste à payer) calculés en une seule requête — la plus ancienne d'abord."""
    invoices = (
        Invoice.objects.filter(
            boutique=boutique, type=Invoice.FACTURE, client__isnull=False,
            status__in=[Invoice.VALIDEE, Invoice.PARTIELLEMENT_PAYEE],
        )
        .select_related("client", "boutique")
        .annotate(paid=Coalesce(Sum("payments__amount"), _ZERO))
        .annotate(due=ExpressionWrapper(F("total_ttc") - F("paid"), output_field=_MONEY))
        .filter(due__gt=0)
        .order_by("issue_date", "created_at")
    )
    if client is not None:
        invoices = invoices.filter(client=client)
    return invoices


def debtors(boutique):
    """Un dict par client qui doit de l'argent, le plus gros débiteur d'abord :
    client, invoices, count, balances [(devise, montant)], total (somme, pour
    le tri), oldest (date de la plus ancienne facture), overdue (nb de
    factures dont l'échéance est passée), last_reminder (datetime ou None)."""
    today = timezone.localdate()
    by_client = {}
    for invoice in unpaid_invoices(boutique):
        entry = by_client.setdefault(invoice.client_id, {
            "client": invoice.client, "invoices": [], "balances": {}, "oldest": invoice.issue_date, "overdue": 0,
        })
        entry["invoices"].append(invoice)
        entry["balances"][invoice.currency] = entry["balances"].get(invoice.currency, Decimal("0")) + invoice.due
        if invoice.due_date and invoice.due_date < today:
            entry["overdue"] += 1

    last = {}
    for reminder in PaymentReminder.objects.filter(boutique=boutique, client_id__in=by_client).order_by("created_at"):
        last[reminder.client_id] = reminder.created_at

    rows = []
    for client_id, entry in by_client.items():
        entry["count"] = len(entry["invoices"])
        entry["balances"] = sorted(entry["balances"].items())
        entry["total"] = sum(amount for _currency, amount in entry["balances"])
        entry["last_reminder"] = last.get(client_id)
        rows.append(entry)
    rows.sort(key=lambda row: -row["total"])
    return rows


def last_reminder_at(client=None, invoice=None):
    """Date de la dernière relance d'un client (relevé ou facture) ou d'une
    facture précise ; None si jamais relancé."""
    reminders = PaymentReminder.objects.all()
    if invoice is not None:
        reminders = reminders.filter(invoice=invoice)
    elif client is not None:
        reminders = reminders.filter(client=client)
    reminder = reminders.first()
    return reminder.created_at if reminder else None


def _money(amount, currency):
    return f"{Decimal(amount):,.0f}".replace(",", " ") + f" {currency}"


def _greeting(client):
    return f"Bonjour {client.name}, " if client.name else "Bonjour, "


def build_invoice_reminder(invoice, *, due, view_link=None):
    """Relance pour UNE facture : montant restant + lien vers la facture."""
    message = (
        f"{_greeting(invoice.client)}petit rappel de {invoice.boutique.name} : il reste "
        f"{_money(due, invoice.currency)} à régler sur votre facture {invoice.number} "
        f"du {invoice.issue_date:%d/%m/%Y} (total {_money(invoice.total_ttc, invoice.currency)})."
    )
    if view_link:
        message += f"\nVoir la facture : {view_link}"
    message += "\nMerci de votre règlement."
    return message


def build_statement(client, invoices, *, boutique, link_for):
    """Relevé d'un client : toutes ses factures impayées et le total dû.
    `link_for(invoice)` donne le lien à joindre à chaque ligne. Au-delà de
    MAX_STATEMENT_LINES factures, le reste est résumé (un message WhatsApp
    interminable ne se lit pas)."""
    invoices = list(invoices)
    totals = {}
    for invoice in invoices:
        totals[invoice.currency] = totals.get(invoice.currency, Decimal("0")) + invoice.due
    total_text = " + ".join(_money(amount, currency) for currency, amount in sorted(totals.items()))

    lines = [f"{_greeting(client)}voici votre relevé chez {boutique.name} : vous nous devez {total_text}."]
    for invoice in invoices[:MAX_STATEMENT_LINES]:
        lines.append(
            f"• {invoice.number} ({invoice.issue_date:%d/%m/%Y}) : reste {_money(invoice.due, invoice.currency)}"
            f" — {link_for(invoice)}"
        )
    hidden = len(invoices) - MAX_STATEMENT_LINES
    if hidden > 0:
        lines.append(f"… et {hidden} autre(s) facture(s).")
    lines.append("Merci de votre règlement.")
    return "\n".join(lines)
