"""Création d'une commande à partir du panier de la vitrine."""

from decimal import Decimal

from django.db import transaction

from apps.core.notifications import notify
from apps.sales import services as sales_services
from apps.sales.models import Client, Invoice
from apps.stock.models import StockLevel
from apps.tenants.models import Membership


class OutOfStock(Exception):
    def __init__(self, names):
        super().__init__(", ".join(names))
        self.names = names


@transaction.atomic
def place_order(*, boutique, lines, data):
    """Crée la commande (EN_ATTENTE, origine EN_LIGNE) et rattache le client
    par son téléphone. Le stock n'est pas déduit ici : il l'est à la
    livraison, comme pour toute commande (voir sales.services.deliver_commande).
    Lève OutOfStock si un produit n'a plus assez de stock."""
    short = []
    for line in lines:
        level = StockLevel.objects.filter(boutique=boutique, product=line["product"]).first()
        if (level.quantity if level else Decimal("0")) < line["quantity"]:
            short.append(line["product"].name)
    if short:
        raise OutOfStock(short)

    phone = data["phone"].strip()
    client = Client.objects.filter(boutique=boutique, phone=phone).first()
    if client is None:
        client = Client.objects.create(
            boutique=boutique, name=data["name"].strip(), phone=phone, address=data["address"].strip(),
        )

    lines_data = [{
        "product": line["product"], "description": line["product"].name,
        "quantity": Decimal(line["quantity"]), "unit_price_ht": line["unit_price_ht"],
        "tva_rate": line["product"].tva_rate,
    } for line in lines]
    if boutique.shop_delivery_fee:
        lines_data.append({
            "product": None, "description": "Frais de livraison", "quantity": Decimal("1"),
            "unit_price_ht": boutique.shop_delivery_fee, "tva_rate": Decimal("0"),
        })

    note = f"Commande en ligne — {data['name'].strip()}"
    if data.get("note"):
        note += f"\n{data['note'].strip()}"
    commande = sales_services.build_invoice(
        boutique=boutique, client=client, type=Invoice.COMMANDE, created_by=None,
        lines_data=lines_data, note=note,
    )
    Invoice.objects.filter(pk=commande.pk).update(
        source=Invoice.EN_LIGNE, delivery_address=data["address"].strip(),
        online_payment_mode=data["payment_mode"],
        online_payment_reference=(data.get("payment_reference") or "").strip(),
    )
    commande.refresh_from_db()

    managers = Membership.objects.filter(
        boutique=boutique, is_active=True,
        role__in=[Membership.ADMIN_COMPTE, Membership.GERANT_BOUTIQUE],
    ).select_related("user")
    for membership in managers:
        notify(
            membership.user,
            f"Nouvelle commande en ligne n°{commande.commande_seq} — {commande.total_ttc} {commande.currency}",
            url=f"/factures/{commande.id}/",
        )
    return commande
