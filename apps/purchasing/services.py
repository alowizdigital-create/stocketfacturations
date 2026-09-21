from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.catalog.models import Product
from apps.stock import services as stock_services
from apps.stock.models import StockLevel, StockMovement

from .models import Expense, Purchase, PurchaseLine


def weighted_average_cost(*, stock_before, current_cost, received_qty, received_cost):
    """Nouveau prix d'achat d'un produit après une réception : moyenne du
    coût actuel et du coût reçu, pondérée par les quantités (méthode du
    « prix moyen pondéré »). Deux cas où l'ancien coût ne compte pas et où le
    nouveau est simplement le coût reçu : aucun coût connu jusque-là, ou aucun
    stock (un stock négatif — possible ici — est ramené à zéro : on ne peut
    pas pondérer par une quantité qu'on n'a pas)."""

    stock_before = max(Decimal(stock_before), Decimal("0"))
    if current_cost is None or stock_before == 0:
        return Decimal(received_cost).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    total_quantity = stock_before + Decimal(received_qty)
    average = (stock_before * Decimal(current_cost) + Decimal(received_qty) * Decimal(received_cost)) / total_quantity
    return average.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


@transaction.atomic
def receive_goods(*, boutique, lines, supplier=None, received_date=None, reference="", note="", created_by=None):
    """Enregistre une réception de marchandises : crée la réception, alimente
    le stock de la boutique (un StockMovement ENTREE par ligne, avec son
    coût) et met à jour le prix d'achat de chaque produit au prix moyen
    pondéré. Tout ou rien : une ligne invalide annule l'ensemble.

    `lines` : liste de dicts {product, quantity, unit_cost}. Le stock pris en
    compte pour la moyenne est celui de TOUTES les boutiques de l'entreprise
    — le prix d'achat est porté par le produit, partagé par le catalogue.
    Le produit est verrouillé le temps du calcul : deux réceptions simultanées
    ne peuvent pas écraser mutuellement leur nouveau prix."""

    if not lines:
        raise ValueError(_("Ajoutez au moins un produit à la réception."))
    if supplier is not None and supplier.compte_id != boutique.compte_id:
        raise ValueError(_("Fournisseur inconnu."))

    received_date = received_date or timezone.localdate()
    purchase = Purchase.objects.create(
        boutique=boutique,
        number=Purchase.generate_number(boutique, received_date),
        supplier=supplier,
        received_date=received_date,
        reference=reference,
        note=note,
        created_by=created_by,
    )

    total = Decimal("0")
    for position, line in enumerate(lines):
        quantity = Decimal(line["quantity"])
        unit_cost = Decimal(line["unit_cost"])
        if quantity <= 0:
            raise ValueError(_("La quantité doit être positive."))
        if unit_cost < 0:
            raise ValueError(_("Le coût unitaire ne peut pas être négatif."))

        product = Product.objects.select_for_update().get(pk=line["product"].pk)
        if product.compte_id != boutique.compte_id:
            raise ValueError(_("Produit inconnu."))

        stock_before = StockLevel.objects.filter(product=product).aggregate(total=Sum("quantity"))["total"] or Decimal("0")
        cost_before = product.purchase_price
        cost_after = weighted_average_cost(
            stock_before=stock_before, current_cost=cost_before, received_qty=quantity, received_cost=unit_cost,
        )

        movement = stock_services.apply_movement(
            boutique=boutique,
            product=product,
            type=StockMovement.ENTREE,
            quantity=quantity,
            unit_cost=unit_cost,
            reason=_("Réception %(number)s") % {"number": purchase.number},
            created_by=created_by,
        )
        # update() : seul le prix change, sans repasser par Product.save()
        # (qui retraite la photo) ni bouger updated_at — ce champ n'est pas
        # synchronisé hors-ligne.
        Product.objects.filter(pk=product.pk).update(purchase_price=cost_after)

        line_total = (quantity * unit_cost).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        PurchaseLine.objects.create(
            purchase=purchase, product=product, quantity=quantity, unit_cost=unit_cost,
            line_total=line_total, cost_before=cost_before, cost_after=cost_after,
            movement=movement, position=position,
        )
        total += line_total

    purchase.total_cost = total
    purchase.save(update_fields=["total_cost", "updated_at"])
    return purchase



@transaction.atomic
def record_expense(*, boutique, category, label, amount, expense_date=None, note="", created_by=None,
                   from_personal_cash=False):
    """Enregistre une dépense de fonctionnement. `from_personal_cash` : elle a
    été payée avec l'argent de la caisse individuelle de `created_by`, qui est
    alors débitée — refusé si le solde ne suffit pas (on ne dépense pas de
    l'argent qu'on n'a pas en caisse, même règle qu'un transfert)."""
    from apps.cashier import services as cashier_services
    from apps.cashier.models import PersonalCashMovement

    amount = Decimal(amount)
    if amount <= 0:
        raise ValueError(_("Le montant doit être positif."))
    if category not in dict(Expense.CATEGORY_CHOICES):
        raise ValueError(_("Catégorie inconnue."))
    label = (label or "").strip()
    if not label:
        raise ValueError(_("Indiquez un libellé."))

    movement = None
    if from_personal_cash:
        if created_by is None:
            raise ValueError(_("Aucune caisse à débiter."))
        if amount > cashier_services.personal_cash_balance(created_by, boutique):
            raise ValueError(_("Solde insuffisant dans votre caisse."))
        movement = PersonalCashMovement.objects.create(
            boutique=boutique, user=created_by, type=PersonalCashMovement.DEBIT,
            kind=PersonalCashMovement.DEPENSE, amount=amount, reason=label, created_by=created_by,
        )
    return Expense.objects.create(
        boutique=boutique, category=category, label=label, amount=amount,
        expense_date=expense_date or timezone.localdate(), note=note,
        cash_movement=movement, created_by=created_by,
    )


@transaction.atomic
def cancel_expense(expense, *, cancelled_by=None):
    """Annule une dépense : elle sort des totaux et, si elle avait été payée
    depuis une caisse individuelle, celle-ci est recréditée (un CREDIT, sans
    effacer le débit d'origine : l'historique montre les deux). Idempotent."""
    from apps.cashier.models import PersonalCashMovement

    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if expense.is_cancelled:
        return expense
    movement = expense.cash_movement
    if movement is not None:
        PersonalCashMovement.objects.create(
            boutique=expense.boutique, user=movement.user, type=PersonalCashMovement.CREDIT,
            kind=PersonalCashMovement.DEPENSE, amount=movement.amount,
            reason=_("Annulation : %(label)s") % {"label": expense.label}, created_by=cancelled_by,
        )
    expense.cancelled_at = timezone.now()
    expense.cancelled_by = cancelled_by
    expense.save(update_fields=["cancelled_at", "cancelled_by", "updated_at"])
    return expense
