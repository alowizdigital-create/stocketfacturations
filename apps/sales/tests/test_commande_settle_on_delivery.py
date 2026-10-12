"""Livrer une commande solde sa facture — voir
apps.sales.services.settle_invoice_on_delivery et deliver_commande."""

from decimal import Decimal

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.sales import services
from apps.sales.models import Invoice, Payment
from apps.sync.tests.factories import BoutiqueFactory
from apps.tenants.models import Membership

pytestmark = pytest.mark.django_db


@pytest.fixture
def boutique():
    return BoutiqueFactory()


@pytest.fixture
def user(boutique):
    u = User.objects.create_user(email="liv@test.com", password="x")
    Membership.objects.create(user=u, boutique=boutique, role=Membership.GERANT_BOUTIQUE, is_active=True)
    return u


def _commande(boutique, **kwargs):
    return services.build_invoice(
        boutique=boutique, client=None, type=Invoice.COMMANDE, created_by=None,
        lines_data=[{"product": None, "description": "Article", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("1000"), "tva_rate": Decimal("0")}],
        **kwargs,
    )


def _facture(commande):
    return commande.conversions.get(type=Invoice.FACTURE)


def test_delivery_settles_the_invoice(boutique, user):
    commande = _commande(boutique)
    invoice = services.deliver_commande(commande, delivered_by=user)
    invoice.refresh_from_db()
    assert invoice.status == Invoice.PAYEE and invoice.balance_due == 0 and invoice.settled_at is not None
    payment = invoice.payments.get()
    assert payment.amount == Decimal("1000") and payment.method == Payment.ESPECES
    assert payment.created_by == user and payment.reference == services.DELIVERY_PAYMENT_REF


def test_delivery_only_collects_the_balance_after_deposit(boutique, user):
    commande = _commande(boutique)
    Payment.objects.create(invoice=commande, boutique=boutique, amount=Decimal("300"), created_by=user)
    invoice = services.deliver_commande(commande, delivered_by=user)
    invoice.refresh_from_db()
    assert invoice.amount_paid == Decimal("1000") and invoice.status == Invoice.PAYEE
    assert sorted(p.amount for p in invoice.payments.all()) == [Decimal("300"), Decimal("700")]


def test_already_converted_commande_is_settled_on_delivery(boutique, user):
    commande = _commande(boutique)
    services.convert_commande_to_invoice(commande, created_by=user)
    commande.refresh_from_db()
    assert _facture(commande).status == Invoice.VALIDEE
    services.deliver_commande(commande, delivered_by=user)
    assert _facture(commande).status == Invoice.PAYEE


def test_delivery_twice_does_not_double_charge(boutique, user):
    commande = _commande(boutique)
    services.deliver_commande(commande, delivered_by=user)
    commande.refresh_from_db()
    services.deliver_commande(commande, delivered_by=user)
    assert _facture(commande).payments.count() == 1


def test_online_mobile_money_order_settled_as_mobile_money(boutique, user):
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(
        source=Invoice.EN_LIGNE, online_payment_mode=Invoice.PAY_MOBILE_MONEY, online_payment_reference="TX42",
    )
    commande.refresh_from_db()
    services.deliver_commande(commande, delivered_by=user)
    payment = _facture(commande).payments.get()
    assert payment.method == Payment.MOBILE_MONEY and "TX42" in payment.reference


def test_undoing_delivery_unsettles_the_invoice(boutique, user):
    commande = _commande(boutique)
    services.deliver_commande(commande, delivered_by=user)
    commande.refresh_from_db()
    services.mark_commande_en_cours(commande, undone_by=user)
    invoice = _facture(commande)
    invoice.refresh_from_db()
    assert invoice.status == Invoice.VALIDEE and invoice.payments.count() == 0 and invoice.balance_due == 1000


def test_undoing_delivery_keeps_manual_payments(boutique, user):
    commande = _commande(boutique)
    Payment.objects.create(invoice=commande, boutique=boutique, amount=Decimal("300"), created_by=user)
    services.deliver_commande(commande, delivered_by=user)
    commande.refresh_from_db()
    services.mark_commande_en_cours(commande, undone_by=user)
    invoice = _facture(commande)
    invoice.refresh_from_db()
    assert invoice.amount_paid == Decimal("300") and invoice.status == Invoice.PARTIELLEMENT_PAYEE


@override_settings(IS_OFFLINE=True)
def test_not_settled_on_offline_station(boutique, user):
    commande = _commande(boutique)
    services.convert_commande_to_invoice(commande, created_by=user)
    commande.refresh_from_db()
    services.deliver_commande(commande, delivered_by=user)
    assert _facture(commande).payments.count() == 0


def test_button_delivers_and_message_says_settled(client, boutique, user):
    client.force_login(user)
    commande = _commande(boutique)
    Invoice.objects.filter(pk=commande.pk).update(delivery_status=Invoice.EN_COURS)
    response = client.post(reverse("sales:commande_advance", args=[commande.id]), {
        "from": Invoice.EN_COURS, "to": Invoice.LIVREE, "next": "",
    }, follow=True)
    assert "soldée" in " ".join(str(m) for m in response.context["messages"])
    assert _facture(commande).status == Invoice.PAYEE
