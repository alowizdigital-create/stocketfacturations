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

    product = forms.ModelChoiceField(queryset=Product.objects.none(), label="Produit")
    type = forms.ChoiceField(choices=TYPE_CHOICES, label="Type de mouvement")
    quantity = forms.DecimalField(
        label="Quantité",
        min_value=0.001,
        max_digits=12,
        decimal_places=3,
        help_text="Toujours positive : le sens (+/-) est déduit du type choisi.",
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
