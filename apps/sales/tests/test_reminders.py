"""Relances de paiement : factures impayées, relevé client, envoi WhatsApp."""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.sales import reminders
from apps.sales.models import Client, Invoice, Payment, PaymentReminder
from apps.sales.whatsapp_api import WhatsAppSendError
from apps.sync.tests.factories import BoutiqueFactory
from apps.tenants.models import Membership

pytestmark = pytest.mark.django_db


@pytest.fixture
def boutique():
    return BoutiqueFactory(country_calling_code="225")


@pytest.fixture
def user(boutique):
    user = User.objects.create_user(email="gerant@test.com", password="x", first_name="Ali", last_name="B")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    return user


@pytest.fixture
def logged(client, user):
    client.force_login(user)
    return client


_seq = iter(range(1, 10_000))


def _client(boutique, name="Awa", phone="0701020304"):
    return Client.objects.create(boutique=boutique, name=name, phone=phone)


def _invoice(boutique, client, total=10000, paid=0, status=Invoice.VALIDEE, **kwargs):
    invoice = Invoice.objects.create(
        boutique=boutique, client=client, type=Invoice.FACTURE, status=status,
        number=f"{boutique.code}-F-{next(_seq):04d}", total_ttc=Decimal(total), currency="XOF", **kwargs,
    )
    if paid:
        Payment.objects.create(invoice=invoice, boutique=boutique, amount=Decimal(paid))
    return invoice


# --- calculs --------------------------------------------------------------

def test_unpaid_invoices_scope(boutique):
    awa = _client(boutique)
    due = _invoice(boutique, awa, 10000, paid=2500, status=Invoice.PARTIELLEMENT_PAYEE)
    _invoice(boutique, awa, 5000, paid=5000, status=Invoice.PAYEE)              # soldée
    _invoice(boutique, awa, 5000, status=Invoice.BROUILLON)                      # pas validée
    _invoice(boutique, awa, 5000, status=Invoice.ANNULEE)                        # annulée
    _invoice(boutique, None, 5000)                                               # sans client
    _invoice(BoutiqueFactory(), _client(BoutiqueFactory()), 5000)                # autre boutique
    Invoice.objects.create(boutique=boutique, client=awa, type=Invoice.DEVIS, status=Invoice.VALIDEE,
                           number="D-1", total_ttc=Decimal("999"), currency="XOF")   # un devis n'est pas une dette
    result = list(reminders.unpaid_invoices(boutique))
    assert [i.pk for i in result] == [due.pk]
    assert result[0].due == Decimal("7500") and result[0].paid == Decimal("2500")


def test_multiple_payments_are_summed_once(boutique):
    awa = _client(boutique)
    invoice = _invoice(boutique, awa, 10000, status=Invoice.PARTIELLEMENT_PAYEE)
    for amount in (1000, 2000, 3000):
        Payment.objects.create(invoice=invoice, boutique=boutique, amount=Decimal(amount))
    assert reminders.unpaid_invoices(boutique).get().due == Decimal("4000")


def test_debtors_grouped_and_sorted(boutique):
    awa, moussa = _client(boutique, "Awa"), _client(boutique, "Moussa", "0505050505")
    _invoice(boutique, awa, 3000)
    _invoice(boutique, awa, 2000, due_date=timezone.localdate() - timedelta(days=3))
    _invoice(boutique, moussa, 20000)
    rows = reminders.debtors(boutique)
    assert [r["client"].name for r in rows] == ["Moussa", "Awa"]
    assert rows[1]["count"] == 2 and rows[1]["total"] == Decimal("5000") and rows[1]["overdue"] == 1
    assert rows[1]["balances"] == [("XOF", Decimal("5000"))]
    assert rows[0]["last_reminder"] is None


# --- messages -------------------------------------------------------------

def test_invoice_message_has_amount_and_link(boutique):
    invoice = _invoice(boutique, _client(boutique), 12500, paid=2500, status=Invoice.PARTIELLEMENT_PAYEE)
    invoice = reminders.unpaid_invoices(boutique).get(pk=invoice.pk)
    text = reminders.build_invoice_reminder(invoice, due=invoice.due, view_link="https://x.test/s/abc/")
    assert "10 000 XOF" in text and invoice.number in text and "https://x.test/s/abc/" in text
    assert "Awa" in text and "12 500 XOF" in text


def test_statement_lists_invoices_and_caps(boutique):
    awa = _client(boutique)
    for _ in range(10):
        _invoice(boutique, awa, 1000)
    invoices = list(reminders.unpaid_invoices(boutique, client=awa))
    text = reminders.build_statement(awa, invoices, boutique=boutique, link_for=lambda i: "L")
    assert "10 000 XOF" in text and text.count("• ") == reminders.MAX_STATEMENT_LINES
    assert "2 autre(s) facture(s)" in text


# --- envoi ----------------------------------------------------------------

@override_settings(TECHSOFT_API_KEY="")
def test_remind_without_api_opens_whatsapp_and_logs(logged, boutique, user):
    invoice = _invoice(boutique, _client(boutique), 8000, paid=3000, status=Invoice.PARTIELLEMENT_PAYEE)
    response = logged.post(reverse("sales:invoice_remind", args=[invoice.id]))
    assert response.status_code == 302 and response.url.startswith("https://wa.me/225701020304?text=")
    assert "5%20000%20XOF" in response.url
    log = PaymentReminder.objects.get()
    assert (log.invoice, log.amount, log.channel, log.sent_by) == (invoice, Decimal("5000"), PaymentReminder.LINK, user)


@override_settings(TECHSOFT_API_KEY="k")
def test_remind_with_api_sends_pdf(logged, boutique):
    invoice = _invoice(boutique, _client(boutique), 8000)
    with mock.patch("apps.sales.views.send_document") as send:
        response = logged.post(reverse("sales:invoice_remind", args=[invoice.id]))
    assert response.status_code == 302 and response.url == reverse("sales:invoice_detail", args=[invoice.id])
    kwargs = send.call_args.kwargs
    assert kwargs["to"] == "225701020304" and "8 000 XOF" in kwargs["message"]
    assert kwargs["document_url"].endswith("/pdf/") and kwargs["filename"].endswith(".pdf")
    assert PaymentReminder.objects.get().channel == PaymentReminder.API


@override_settings(TECHSOFT_API_KEY="k")
def test_remind_api_failure_logs_nothing(logged, boutique):
    invoice = _invoice(boutique, _client(boutique), 8000)
    with mock.patch("apps.sales.views.send_document", side_effect=WhatsAppSendError("boom")):
        logged.post(reverse("sales:invoice_remind", args=[invoice.id]))
    assert not PaymentReminder.objects.exists()


@override_settings(TECHSOFT_API_KEY="k")
def test_client_statement_reminder_is_text_only(logged, boutique):
    awa = _client(boutique)
    _invoice(boutique, awa, 3000)
    _invoice(boutique, awa, 4000)
    with mock.patch("apps.sales.views.send_document") as send:
        logged.post(reverse("sales:client_remind", args=[awa.id]))
    kwargs = send.call_args.kwargs
    assert kwargs.get("document_url") is None and "7 000 XOF" in kwargs["message"]
    log = PaymentReminder.objects.get()
    assert log.invoice is None and log.amount == Decimal("7000")


@override_settings(TECHSOFT_API_KEY="k")
@pytest.mark.parametrize("phone", ["", "   "])
def test_no_phone_no_reminder(logged, boutique, phone):
    invoice = _invoice(boutique, _client(boutique, phone=phone), 8000)
    with mock.patch("apps.sales.views.send_document") as send:
        logged.post(reverse("sales:invoice_remind", args=[invoice.id]))
    assert not send.called and not PaymentReminder.objects.exists()


@override_settings(TECHSOFT_API_KEY="k")
def test_paid_or_foreign_invoice_is_not_reminded(logged, boutique):
    paid = _invoice(boutique, _client(boutique), 1000, paid=1000, status=Invoice.PAYEE)
    other = BoutiqueFactory()
    foreign = _invoice(other, _client(other), 1000)
    with mock.patch("apps.sales.views.send_document") as send:
        logged.post(reverse("sales:invoice_remind", args=[paid.id]))
        logged.post(reverse("sales:invoice_remind", args=[foreign.id]))
    assert not send.called and not PaymentReminder.objects.exists()


def test_remind_is_post_only_and_login_required(client, logged, boutique):
    invoice = _invoice(boutique, _client(boutique), 1000)
    assert logged.get(reverse("sales:invoice_remind", args=[invoice.id])).status_code == 405
    client.logout()
    assert client.post(reverse("sales:invoice_remind", args=[invoice.id])).status_code == 302
    assert not PaymentReminder.objects.exists()


@override_settings(IS_OFFLINE=True, TECHSOFT_API_KEY="k")
def test_no_reminder_from_offline_workstation(logged, boutique):
    invoice = _invoice(boutique, _client(boutique), 1000)
    with mock.patch("apps.sales.views.send_document") as send:
        logged.post(reverse("sales:invoice_remind", args=[invoice.id]))
    assert not send.called and not PaymentReminder.objects.exists()


# --- pages ----------------------------------------------------------------

def test_pages_render(logged, boutique):
    awa = _client(boutique)
    invoice = _invoice(boutique, awa, 9000, paid=1000, status=Invoice.PARTIELLEMENT_PAYEE)
    PaymentReminder.objects.create(
        boutique=boutique, client=awa, invoice=invoice, amount=Decimal("8000"), channel=PaymentReminder.LINK,
    )
    debtors = logged.get(reverse("sales:debtor_list")).content.decode()
    assert "Awa" in debtors and "8000" in debtors and "Relancer" in debtors
    statement = logged.get(reverse("sales:client_statement", args=[awa.id])).content.decode()
    assert invoice.number in statement and "8000" in statement
    detail = logged.get(reverse("sales:invoice_detail", args=[invoice.id])).content.decode()
    assert reverse("sales:invoice_remind", args=[invoice.id]) in detail
    unpaid = logged.get(reverse("sales:invoice_list"), {"unpaid": "1"}).content.decode()
    assert invoice.number in unpaid and "8000" in unpaid
    assert logged.get(reverse("sales:client_list")).status_code == 200


def test_statement_of_other_boutique_client_404(logged):
    other = BoutiqueFactory()
    stranger = _client(other)
    assert logged.get(reverse("sales:client_statement", args=[stranger.id])).status_code == 404
    assert logged.post(reverse("sales:client_remind", args=[stranger.id])).status_code == 404


def test_open_redirect_is_refused(logged, boutique):
    invoice = _invoice(boutique, _client(boutique), 1000)
    with override_settings(TECHSOFT_API_KEY="k"), mock.patch("apps.sales.views.send_document"):
        response = logged.post(reverse("sales:invoice_remind", args=[invoice.id]), {"next": "https://evil.test/"})
    assert response.url == reverse("sales:invoice_detail", args=[invoice.id])
