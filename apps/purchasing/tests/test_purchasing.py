"""Prix d'achat, fournisseurs, réception de marchandises et rapport de marge."""

import json
from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product, Unit
from apps.purchasing import services
from apps.cashier import services as cashier_services
from apps.cashier.models import PersonalCashMovement
from apps.purchasing.models import Expense, Purchase, Supplier
from apps.sales import services as sales_services
from apps.sales.models import Invoice
from apps.sales.reports import margin_report
from apps.stock import services as stock_services
from apps.stock.models import StockLevel, StockMovement
from apps.sync.tests.factories import BoutiqueFactory, ProductFactory
from apps.tenants.models import Membership

pytestmark = pytest.mark.django_db


@pytest.fixture
def boutique():
    return BoutiqueFactory()


def _user(boutique, role, email):
    user = User.objects.create_user(email=email, password="x")
    Membership.objects.create(user=user, boutique=boutique, role=role, is_active=True)
    return user


@pytest.fixture
def admin_client(client, boutique):
    client.force_login(_user(boutique, Membership.ADMIN_COMPTE, "admin@test.com"))
    return client


@pytest.fixture
def cashier_client(client, boutique):
    client.force_login(_user(boutique, Membership.CAISSIER, "caissier@test.com"))
    return client


def _product(compte, **kwargs):
    unit = Unit.objects.get_or_create(compte=compte, name="Pièce", defaults={"symbol": "pc"})[0]
    return ProductFactory(compte=compte, unit=unit, **kwargs)


def _stock(boutique, product, quantity):
    stock_services.apply_movement(
        boutique=boutique, product=product, type=StockMovement.ENTREE, quantity=quantity, reason="test",
    )


def _qty(boutique, product):
    return StockLevel.objects.get(boutique=boutique, product=product).quantity


def _receive(boutique, product, quantity, cost, **kwargs):
    return services.receive_goods(
        boutique=boutique, lines=[{"product": product, "quantity": Decimal(quantity), "unit_cost": Decimal(cost)}],
        **kwargs,
    )


# --- prix moyen pondéré ---------------------------------------------------

@pytest.mark.parametrize("stock,current,qty,cost,expected", [
    (0, None, 10, 500, 500),        # premier coût connu
    (0, 400, 10, 500, 500),         # rien en stock : l'ancien coût ne compte pas
    (10, None, 10, 500, 500),       # stock sans coût connu : on ne pondère pas
    (10, 400, 10, 500, 450),        # moyenne simple à quantités égales
    (30, 100, 10, 200, 125),        # (30*100 + 10*200) / 40
    (-5, 400, 10, 500, 500),        # stock négatif ramené à zéro
    (3, 100, 1, 200, 125),          # (300+200)/4 = 125
    (2, 100, 1, 150, 117),          # 116,67 arrondi
])
def test_weighted_average(stock, current, qty, cost, expected):
    result = services.weighted_average_cost(
        stock_before=Decimal(stock), current_cost=None if current is None else Decimal(current),
        received_qty=Decimal(qty), received_cost=Decimal(cost),
    )
    assert result == Decimal(expected)


# --- réception ------------------------------------------------------------

def test_receive_goods_feeds_stock_and_cost(boutique):
    product = _product(boutique.compte)
    purchase = _receive(boutique, product, 10, 500)
    product.refresh_from_db()
    assert product.purchase_price == Decimal("500")
    assert _qty(boutique, product) == Decimal("10")
    assert purchase.total_cost == Decimal("5000")
    assert purchase.number.startswith(f"{boutique.code}-ACH-")
    movement = purchase.lines.get().movement
    assert movement.type == StockMovement.ENTREE and movement.unit_cost == Decimal("500")


def test_second_reception_averages_cost(boutique):
    product = _product(boutique.compte)
    _receive(boutique, product, 10, 400)
    purchase = _receive(boutique, product, 10, 500)
    product.refresh_from_db()
    assert product.purchase_price == Decimal("450")
    line = purchase.lines.get()
    assert (line.cost_before, line.cost_after) == (Decimal("400"), Decimal("450"))
    assert _qty(boutique, product) == Decimal("20")


def test_average_counts_stock_of_all_boutiques(boutique):
    other = BoutiqueFactory(compte=boutique.compte)
    product = _product(boutique.compte)
    _receive(other, product, 10, 400)                 # 10 unités à 400 dans l'autre boutique
    _receive(boutique, product, 10, 500)              # 10 unités à 500 ici
    product.refresh_from_db()
    assert product.purchase_price == Decimal("450")


def test_receive_is_all_or_nothing(boutique):
    good = _product(boutique.compte)
    with pytest.raises(ValueError):
        services.receive_goods(boutique=boutique, lines=[
            {"product": good, "quantity": Decimal("5"), "unit_cost": Decimal("100")},
            {"product": good, "quantity": Decimal("0"), "unit_cost": Decimal("100")},
        ])
    good.refresh_from_db()
    assert good.purchase_price is None
    assert not Purchase.objects.exists()
    assert not StockLevel.objects.filter(product=good).exists()


def test_receive_rejects_foreign_product_and_supplier(boutique):
    foreign = _product(BoutiqueFactory().compte)
    with pytest.raises(ValueError):
        _receive(boutique, foreign, 1, 100)
    foreign_supplier = Supplier.objects.create(compte=BoutiqueFactory().compte, name="Autre")
    with pytest.raises(ValueError):
        _receive(boutique, _product(boutique.compte), 1, 100, supplier=foreign_supplier)


def test_purchase_numbers_are_sequential(boutique):
    product = _product(boutique.compte)
    first = _receive(boutique, product, 1, 100)
    second = _receive(boutique, product, 1, 100)
    assert first.number != second.number and second.number.endswith("0002")


# --- vues -----------------------------------------------------------------

def _post_purchase(client, product, quantity=5, cost=200, **extra):
    data = {
        "lines_json": json.dumps([{"product_id": str(product.id), "quantity": quantity, "unit_cost": cost}]),
        "received_date": timezone.localdate().isoformat(),
        **extra,
    }
    return client.post(reverse("purchasing:purchase_create"), data)


def test_purchase_create_view(admin_client, boutique):
    product = _product(boutique.compte)
    supplier = Supplier.objects.create(compte=boutique.compte, name="Grossiste")
    response = _post_purchase(admin_client, product, supplier=str(supplier.id), reference="BL-42")
    purchase = Purchase.objects.get()
    assert response.status_code == 302 and response.url == reverse("purchasing:purchase_detail", args=[purchase.id])
    assert purchase.supplier == supplier and purchase.reference == "BL-42"
    assert _qty(boutique, product) == Decimal("5")
    assert admin_client.get(response.url).status_code == 200
    assert admin_client.get(reverse("purchasing:purchase_list")).status_code == 200
    assert admin_client.get(reverse("purchasing:purchase_create")).status_code == 200


@pytest.mark.parametrize("quantity,cost", [(0, 100), (-3, 100), (2.5, 100), (5, -1), (5, 10.5)])
def test_purchase_create_rejects_bad_numbers(admin_client, boutique, quantity, cost):
    product = _product(boutique.compte)
    response = _post_purchase(admin_client, product, quantity=quantity, cost=cost)
    assert response.status_code == 200 and not Purchase.objects.exists()


def test_purchase_create_rejects_future_date_and_keeps_lines(admin_client, boutique):
    product = _product(boutique.compte)
    tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
    response = _post_purchase(admin_client, product, received_date=tomorrow)
    assert response.status_code == 200 and not Purchase.objects.exists()
    assert product.name in response.content.decode()   # les lignes sont réaffichées


def test_purchase_create_ignores_other_company_product(admin_client, boutique):
    foreign = _product(BoutiqueFactory().compte)
    response = _post_purchase(admin_client, foreign)
    assert response.status_code == 200 and not Purchase.objects.exists()


def test_cashier_cannot_reach_purchasing_or_margin(cashier_client, boutique):
    product = _product(boutique.compte)
    for name in ("purchasing:purchase_list", "purchasing:purchase_create", "purchasing:supplier_list",
                 "sales:margin_report"):
        assert cashier_client.get(reverse(name)).status_code in (302, 403)
    assert _post_purchase(cashier_client, product).status_code in (302, 403)
    assert not Purchase.objects.exists()


def test_supplier_quick_create(admin_client, boutique):
    url = reverse("purchasing:supplier_quick_create")
    ok = admin_client.post(url, {"name": "Nouveau", "phone": "0102"})
    assert ok.status_code == 200 and ok.json()["name"] == "Nouveau"
    assert Supplier.objects.get(name="Nouveau").is_active   # sinon la réception le refuserait
    product = _product(boutique.compte)
    assert _post_purchase(admin_client, product, supplier=ok.json()["id"]).status_code == 302
    dup = admin_client.post(url, {"name": "nouveau"})
    assert dup.status_code == 400
    assert admin_client.get(url).status_code == 405


def test_supplier_name_unique_per_company_only(boutique):
    Supplier.objects.create(compte=boutique.compte, name="Grossiste")
    Supplier.objects.create(compte=BoutiqueFactory().compte, name="Grossiste")   # autre entreprise : permis


# --- coût dans le catalogue -----------------------------------------------

def test_cost_hidden_from_cashier_in_search(cashier_client, boutique):
    product = _product(boutique.compte, purchase_price=Decimal("300"))
    _stock(boutique, product, 3)
    url = reverse("catalog:product_search")
    assert "purchase_price" not in cashier_client.get(url, {"include_cost": "1"}).json()["results"][0]
    body = cashier_client.get(reverse("catalog:product_detail", args=[product.id])).content.decode()
    assert "Marge unitaire" not in body


def test_cost_visible_to_admin_and_barcode_out_of_stock(admin_client, boutique):
    product = _product(boutique.compte, purchase_price=Decimal("300"), barcode="123456789")
    result = admin_client.get(reverse("catalog:product_search"), {"include_cost": "1", "include_out_of_stock": "1"})
    assert result.json()["results"][0]["purchase_price"] == 300.0
    # sans le paramètre : un produit à zéro n'est pas vendable ; en réception il est trouvé
    url = reverse("catalog:product_by_barcode")
    assert admin_client.get(url, {"code": "123456789"}).json()["status"] == "out_of_stock"
    found = admin_client.get(url, {"code": "123456789", "include_out_of_stock": "1", "include_cost": "1"}).json()
    assert found["status"] == "found" and found["product"]["purchase_price"] == 300.0
    body = admin_client.get(reverse("catalog:product_detail", args=[product.id])).content.decode()
    assert "Marge unitaire" in body


# --- marge ------------------------------------------------------------------

def _confirmed_sale(boutique, product, quantity, price, sale_date=None):
    sale = sales_services.build_sale(
        boutique=boutique, client=None, created_by=None, sale_date=sale_date,
        lines_data=[{
            "product": product, "description": product.name, "quantity": Decimal(quantity),
            "unit_price_ht": Decimal(price), "tva_rate": Decimal("0"),
        }],
    )
    return sales_services.confirm_sale(sale)


def test_sale_line_snapshots_cost(boutique):
    product = _product(boutique.compte, purchase_price=Decimal("300"))
    _stock(boutique, product, 10)
    sale = _confirmed_sale(boutique, product, 2, 500)
    product.purchase_price = Decimal("999")     # un changement ultérieur ne réécrit pas l'histoire
    product.save()
    assert sale.lines.get().unit_cost == Decimal("300")


def test_margin_report(boutique):
    today = timezone.localdate()
    known = _product(boutique.compte, purchase_price=Decimal("300"))
    unknown = _product(boutique.compte)        # aucun prix d'achat
    _stock(boutique, known, 20)
    _stock(boutique, unknown, 20)
    _confirmed_sale(boutique, known, 2, 500)   # ventes 1000, coût 600, marge 400
    _confirmed_sale(boutique, known, 1, 500)   # ventes 500, coût 300, marge 200
    _confirmed_sale(boutique, unknown, 4, 250)  # ventes 1000, coût inconnu

    report = margin_report(boutique, today, today)
    totals = report["totals"]
    assert totals["revenue"] == Decimal("2500")
    assert totals["revenue_known"] == Decimal("1500")
    assert totals["cost"] == Decimal("900")
    assert totals["margin"] == Decimal("600")
    assert totals["margin_percent"] == Decimal("40.0")
    assert totals["revenue_unknown_cost"] == Decimal("1000")

    by_name = {row["name"]: row for row in report["rows"]}
    assert by_name[known.name]["quantity"] == Decimal("3")
    assert by_name[known.name]["margin"] == Decimal("600")
    assert by_name[unknown.name]["margin_percent"] is None      # jamais « 100 % » par défaut
    assert report["rows"][-1]["name"] == unknown.name           # inconnus en fin de liste


def test_margin_report_period_and_status(boutique):
    today = timezone.localdate()
    product = _product(boutique.compte, purchase_price=Decimal("100"))
    _stock(boutique, product, 50)
    _confirmed_sale(boutique, product, 1, 300, sale_date=today - timedelta(days=40))
    draft = sales_services.build_sale(
        boutique=boutique, client=None, created_by=None,
        lines_data=[{"product": product, "description": "x", "quantity": Decimal("1"),
                     "unit_price_ht": Decimal("300"), "tva_rate": Decimal("0")}],
    )
    assert draft.status == "BROUILLON"
    assert margin_report(boutique, today, today)["totals"]["revenue"] == 0
    assert margin_report(boutique, today - timedelta(days=60), today)["totals"]["revenue"] == Decimal("300")


def test_margin_report_is_scoped_to_boutique(boutique):
    other = BoutiqueFactory(compte=boutique.compte)
    product = _product(boutique.compte, purchase_price=Decimal("100"))
    _stock(other, product, 5)
    _confirmed_sale(other, product, 1, 300)
    today = timezone.localdate()
    assert margin_report(boutique, today, today)["totals"]["revenue"] == 0


def test_margin_report_view_and_dashboard(admin_client, boutique):
    product = _product(boutique.compte, purchase_price=Decimal("100"))
    _stock(boutique, product, 5)
    _confirmed_sale(boutique, product, 1, 300)
    response = admin_client.get(reverse("sales:margin_report"))
    assert response.status_code == 200 and product.name in response.content.decode()
    assert admin_client.get(reverse("sales:margin_report"), {"from": "n'importe quoi"}).status_code == 200
    assert "Bénéfice net du mois" in admin_client.get(reverse("core:home")).content.decode()


def test_dashboard_hides_margin_from_cashier(cashier_client, boutique):
    product = _product(boutique.compte, purchase_price=Decimal("100"))
    _stock(boutique, product, 5)
    _confirmed_sale(boutique, product, 1, 300)
    body = cashier_client.get(reverse("core:home")).content.decode()
    assert "Bénéfice net du mois" not in body


# --- dépenses de fonctionnement --------------------------------------------

def _expense(boutique, amount=10000, **kwargs):
    kwargs.setdefault("category", Expense.LOYER)
    kwargs.setdefault("label", "Loyer")
    return services.record_expense(boutique=boutique, amount=Decimal(amount), **kwargs)


def test_expense_reduces_net_profit(boutique):
    today = timezone.localdate()
    product = _product(boutique.compte, purchase_price=Decimal("300"))
    _stock(boutique, product, 10)
    _confirmed_sale(boutique, product, 2, 500)          # marge 400
    _expense(boutique, 150, category=Expense.TRANSPORT, label="Carburant")
    _expense(boutique, 100, category=Expense.TRANSPORT, label="Taxi")
    _expense(boutique, 50, category=Expense.AUTRE, label="Divers")
    report = margin_report(boutique, today, today)
    assert report["totals"]["margin"] == Decimal("400")
    assert report["totals"]["expenses_total"] == Decimal("300")
    assert report["totals"]["net_profit"] == Decimal("100")
    assert report["expenses_by_category"][0] == ("Transport", Decimal("250"))


def test_expense_without_sales_gives_negative_profit(boutique):
    today = timezone.localdate()
    _expense(boutique, 5000)
    assert margin_report(boutique, today, today)["totals"]["net_profit"] == Decimal("-5000")


def test_expense_period_boutique_and_cancellation(boutique):
    today = timezone.localdate()
    other = BoutiqueFactory(compte=boutique.compte)
    _expense(other, 999)
    _expense(boutique, 100, expense_date=today - timedelta(days=40))
    kept = _expense(boutique, 200)
    gone = _expense(boutique, 700)
    services.cancel_expense(gone)
    services.cancel_expense(gone)   # idempotent
    assert margin_report(boutique, today, today)["totals"]["expenses_total"] == Decimal("200")
    assert kept.pk and Expense.objects.filter(pk=gone.pk).exists()   # jamais supprimée


@pytest.mark.parametrize("kwargs", [
    {"amount": 0}, {"amount": -5}, {"label": "  "}, {"category": "NOPE"},
])
def test_expense_validation(boutique, kwargs):
    with pytest.raises(ValueError):
        _expense(boutique, **kwargs)
    assert not Expense.objects.exists()


def test_expense_from_personal_cash_debits_and_refunds(boutique):
    user = _user(boutique, Membership.GERANT_BOUTIQUE, "gerant@test.com")
    PersonalCashMovement.objects.create(
        boutique=boutique, user=user, type=PersonalCashMovement.CREDIT, kind=PersonalCashMovement.TRANSFERT, amount=Decimal("1000"),
    )
    expense = _expense(boutique, 400, created_by=user, from_personal_cash=True)
    assert cashier_services.personal_cash_balance(user, boutique) == Decimal("600")
    with pytest.raises(ValueError):   # solde insuffisant : rien n'est enregistré
        _expense(boutique, 601, created_by=user, from_personal_cash=True)
    assert Expense.objects.count() == 1
    services.cancel_expense(expense, cancelled_by=user)
    services.cancel_expense(expense, cancelled_by=user)
    assert cashier_services.personal_cash_balance(user, boutique) == Decimal("1000")


def test_expense_views(admin_client, boutique):
    url = reverse("purchasing:expense_create")
    assert admin_client.get(url).status_code == 200
    data = {"category": "LOYER", "label": "Loyer sept", "amount": "25000", "expense_date": timezone.localdate().isoformat()}
    assert admin_client.post(url, data).status_code == 302
    expense = Expense.objects.get()
    assert expense.created_by.email == "admin@test.com" and expense.cash_movement is None
    assert "Loyer sept" in admin_client.get(reverse("purchasing:expense_list")).content.decode()
    bad = admin_client.post(url, {**data, "expense_date": (timezone.localdate() + timedelta(days=2)).isoformat()})
    assert bad.status_code == 200 and Expense.objects.count() == 1
    assert admin_client.get(reverse("purchasing:expense_cancel", args=[expense.id])).status_code == 405
    admin_client.post(reverse("purchasing:expense_cancel", args=[expense.id]))
    expense.refresh_from_db()
    assert expense.is_cancelled


def test_expense_cashier_and_gerant_permissions(client, boutique):
    cashier = _user(boutique, Membership.CAISSIER, "c2@test.com")
    client.force_login(cashier)
    data = {"category": "LOYER", "label": "x", "amount": "5", "expense_date": timezone.localdate().isoformat()}
    client.post(reverse("purchasing:expense_create"), data)
    assert not Expense.objects.exists()

    expense = _expense(boutique)
    gerant = _user(boutique, Membership.GERANT_BOUTIQUE, "g2@test.com")
    client.force_login(gerant)
    assert client.get(reverse("purchasing:expense_list")).status_code == 200
    client.post(reverse("purchasing:expense_cancel", args=[expense.id]))   # réservé à l'administrateur
    expense.refresh_from_db()
    assert not expense.is_cancelled


# --- frais de livraison = dépense -------------------------------------------

def _commande(boutique, number):
    return Invoice.objects.create(
        boutique=boutique, type=Invoice.COMMANDE, number=number, total_ttc=Decimal("5000"), currency="XOF",
    )


def test_delivery_fee_counts_as_expense(boutique):
    today = timezone.localdate()
    boutique.delivery_price = Decimal("1500")
    boutique.save()
    driver = _user(boutique, Membership.CAISSIER, "livreur@test.com")
    for n in ("C1", "C2"):
        cashier_services.charge_delivery_fee(_commande(boutique, n), delivered_by=driver)
    _expense(boutique, 500, category=Expense.TRANSPORT, label="Taxi")

    totals = margin_report(boutique, today, today)["totals"]
    assert totals["delivery_fees"] == Decimal("3000") and totals["delivery_count"] == 2
    assert totals["expenses_total"] == Decimal("3500")
    assert totals["net_profit"] == Decimal("-3500")
    by_category = dict(margin_report(boutique, today, today)["expenses_by_category"])
    assert by_category["Frais de livraison"] == Decimal("3000")


def test_cancelled_delivery_is_no_longer_an_expense(boutique):
    today = timezone.localdate()
    boutique.delivery_price = Decimal("1500")
    boutique.save()
    driver = _user(boutique, Membership.CAISSIER, "livreur2@test.com")
    commande = _commande(boutique, "C3")
    cashier_services.charge_delivery_fee(commande, delivered_by=driver)
    assert margin_report(boutique, today, today)["totals"]["delivery_fees"] == Decimal("1500")
    cashier_services.refund_delivery_fee(commande)                       # livraison annulée
    assert margin_report(boutique, today, today)["totals"]["delivery_fees"] == 0
    cashier_services.charge_delivery_fee(commande, delivered_by=driver)  # relivrée : comptée une seule fois
    totals = margin_report(boutique, today, today)["totals"]
    assert totals["delivery_fees"] == Decimal("1500") and totals["delivery_count"] == 1


def test_delivery_fee_period_and_boutique(boutique):
    today = timezone.localdate()
    boutique.delivery_price = Decimal("1000")
    boutique.save()
    driver = _user(boutique, Membership.CAISSIER, "livreur3@test.com")
    cashier_services.charge_delivery_fee(_commande(boutique, "C4"), delivered_by=driver)
    other = BoutiqueFactory(compte=boutique.compte, delivery_price=Decimal("700"))
    cashier_services.charge_delivery_fee(_commande(other, "C5"), delivered_by=driver)
    tomorrow = today + timedelta(days=1)
    assert margin_report(boutique, today, today)["totals"]["delivery_fees"] == Decimal("1000")
    assert margin_report(boutique, tomorrow, tomorrow + timedelta(days=5))["totals"]["delivery_fees"] == 0
    assert margin_report(boutique, today - timedelta(days=5), today - timedelta(days=1))["totals"]["delivery_fees"] == 0


def test_delivery_fee_in_expense_list_and_dashboard(admin_client, boutique):
    boutique.delivery_price = Decimal("1200")
    boutique.save()
    driver = _user(boutique, Membership.CAISSIER, "livreur4@test.com")
    cashier_services.charge_delivery_fee(_commande(boutique, "C6"), delivered_by=driver)
    body = admin_client.get(reverse("purchasing:expense_list")).content.decode()
    assert "Frais de livraison" in body and "1200" in body
    assert "Frais de livraison" in admin_client.get(reverse("sales:margin_report")).content.decode()
    assert admin_client.get(reverse("core:home")).status_code == 200
