from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.catalog.models import Product
from apps.core.forms import BootstrapFormMixin

from .models import StockMovement


class StockMovementForm(BootstrapFormMixin, forms.Form):
    TYPE_CHOICES = [
        (StockMovement.ENTREE, _("Entrée")),
        (StockMovement.SORTIE, _("Sortie")),
        (StockMovement.AJUSTEMENT, _("Ajustement")),
    ]

    # La sélection du produit se fait via la recherche en direct en JS (même
    # principe que l'écran de vente/devis) et le type via des boutons d'action
    # directs (voir movement_form.html) : ces deux champs ne portent que la
    # valeur choisie, pas de rendu natif <select>.
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(), label=_("Produit"), widget=forms.HiddenInput()
    )
    type = forms.ChoiceField(
        choices=TYPE_CHOICES, label=_("Type de mouvement"),
        initial=StockMovement.ENTREE, widget=forms.HiddenInput(),
    )
    quantity = forms.DecimalField(
        label=_("Quantité"),
        min_value=1,
        # max_digits=12,
        # decimal_places=,
        # help_text="Toujours positive : le sens (+/-) est déduit du type choisi.",
    )
    reason = forms.CharField(label=_("Motif"), max_length=255, required=False)

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

    quantity = forms.DecimalField(label=_("Quantité"), min_value=Decimal("0.001"), max_digits=12, decimal_places=3)
    reason = forms.CharField(label=_("Motif (optionnel)"), max_length=255, required=False)
