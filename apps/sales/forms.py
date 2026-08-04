from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.catalog.models import Product
from apps.core.forms import BootstrapFormMixin

from .models import Client, Invoice, Payment


class ProductChoiceWidget(forms.Select):
    """Select produit qui porte le prix/la TVA/l'unité de chaque option en
    attributs `data-*`, pour que le JS du formulaire de devis puisse
    pré-remplir automatiquement ces champs à la sélection — sans requête
    supplémentaire, tout est déjà dans le HTML rendu par le serveur."""

    def __init__(self, *args, products=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.products_by_id = {str(p.pk): p for p in (products or [])}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        product = self.products_by_id.get(str(value))
        if product is not None:
            option["attrs"]["data-price"] = str(product.default_sale_price)
            option["attrs"]["data-tva"] = str(product.tva_rate)
            option["attrs"]["data-unit"] = str(product.unit)
        return option


class ClientForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "phone", "email", "address", "nif"]


class InvoiceForm(BootstrapFormMixin, forms.Form):
    client = forms.ModelChoiceField(queryset=Client.objects.none(), required=False, label="Client")
    discount_amount = forms.IntegerField(
        label="Remise globale (FCFA)", min_value=0, required=False, initial=0,
    )

    def __init__(self, *args, boutique=None, **kwargs):
        super().__init__(*args, **kwargs)
        if boutique is not None:
            self.fields["client"].queryset = Client.objects.filter(boutique=boutique).order_by("name")

    def clean_discount_amount(self):
        return self.cleaned_data.get("discount_amount") or 0


class SaleForm(BootstrapFormMixin, forms.Form):
    # Champ caché : la sélection se fait via la recherche par nom en JS
    # (voir sale_form.html), qui écrit l'id du client choisi ici.
    client = forms.ModelChoiceField(
        queryset=Client.objects.none(), required=False, label="Client", widget=forms.HiddenInput()
    )

    def __init__(self, *args, boutique=None, **kwargs):
        super().__init__(*args, **kwargs)
        if boutique is not None:
            self.fields["client"].queryset = Client.objects.filter(boutique=boutique).order_by("name")


class InvoiceLineForm(BootstrapFormMixin, forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(), required=False, label="Produit",
        widget=ProductChoiceWidget(),
    )
    description = forms.CharField(max_length=255, label="Description")
    quantity = forms.IntegerField(
        min_value=1, initial=1, label="Quantité", widget=forms.NumberInput(attrs={"step": "1"})
    )
    unit_price_ht = forms.DecimalField(max_digits=12, decimal_places=0, min_value=Decimal("0"), label="Prix HT")
    tva_rate = forms.DecimalField(
        max_digits=5, decimal_places=2, min_value=Decimal("0"), initial=Decimal("0"), label="TVA %"
    )
    discount_amount = forms.IntegerField(
        label="Remise (FCFA)", min_value=0, required=False, initial=0,
    )

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        if compte is not None:
            queryset = Product.objects.filter(compte=compte, is_active=True).select_related("unit")
            self.fields["product"].queryset = queryset
            self.fields["product"].widget.products_by_id = {str(p.pk): p for p in queryset}

    def clean_discount_amount(self):
        return self.cleaned_data.get("discount_amount") or 0


InvoiceLineFormSet = formset_factory(InvoiceLineForm, extra=1, can_delete=True)


class PaymentForm(BootstrapFormMixin, forms.Form):
    amount = forms.DecimalField(max_digits=14, decimal_places=0, min_value=Decimal("1"))
    method = forms.ChoiceField(choices=Payment.METHOD_CHOICES, initial=Payment.ESPECES)
    reference = forms.CharField(max_length=100, required=False)
