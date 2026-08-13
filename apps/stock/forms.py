from decimal import Decimal

from django import forms

from apps.catalog.models import Product
from apps.core.forms import BootstrapFormMixin

from .models import StockMovement


class StockMovementForm(BootstrapFormMixin, forms.Form):
    TYPE_CHOICES = [
        (StockMovement.ENTREE, "Entrée"),
        (StockMovement.SORTIE, "Sortie"),
        (StockMovement.AJUSTEMENT, "Ajustement"),
    ]

    # La sélection du produit se fait via la recherche en direct en JS (même
    # principe que l'écran de vente/devis) et le type via des boutons d'action
    # directs (voir movement_form.html) : ces deux champs ne portent que la
    # valeur choisie, pas de rendu natif <select>.
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(), label="Produit", widget=forms.HiddenInput()
    )
    type = forms.ChoiceField(
        choices=TYPE_CHOICES, label="Type de mouvement",
        initial=StockMovement.ENTREE, widget=forms.HiddenInput(),
    )
    quantity = forms.DecimalField(
        label="Quantité",
        min_value=1,
        # max_digits=12,
        # decimal_places=,
        # help_text="Toujours positive : le sens (+/-) est déduit du type choisi.",
    )
    reason = forms.CharField(label="Motif", max_length=255, required=False)

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        if compte is not None:
            self.fields["product"].queryset = Product.objects.filter(compte=compte, is_active=True)

    def signed_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if self.cleaned_data["type"] == StockMovement.SORTIE:
            return -quantity
        return quantity


class ProductQuickMovementForm(BootstrapFormMixin, forms.Form):
    """Mouvement rapide (compléter/retirer) depuis la fiche produit — le
    produit est déjà connu de l'URL, pas besoin de le resélectionner comme
    dans le formulaire générique de mouvement."""

    quantity = forms.DecimalField(label="Quantité", min_value=Decimal("0.001"), max_digits=12, decimal_places=3)
    reason = forms.CharField(label="Motif (optionnel)", max_length=255, required=False)
