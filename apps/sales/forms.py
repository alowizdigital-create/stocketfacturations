from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.catalog.models import Product
from apps.core.forms import BootstrapFormMixin

from .models import Client, Invoice, Payment


class ClientForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "phone", "email", "address", "nif"]


class InvoiceForm(BootstrapFormMixin, forms.Form):
    client = forms.ModelChoiceField(queryset=Client.objects.none(), required=False, label="Client")

    def __init__(self, *args, boutique=None, **kwargs):
        super().__init__(*args, **kwargs)
        if boutique is not None:
            self.fields["client"].queryset = Client.objects.filter(boutique=boutique).order_by("name")


class SaleForm(BootstrapFormMixin, forms.Form):
    client = forms.ModelChoiceField(queryset=Client.objects.none(), required=False, label="Client")

    def __init__(self, *args, boutique=None, **kwargs):
        super().__init__(*args, **kwargs)
        if boutique is not None:
            self.fields["client"].queryset = Client.objects.filter(boutique=boutique).order_by("name")


class InvoiceLineForm(BootstrapFormMixin, forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none(), required=False)
    description = forms.CharField(max_length=255)
    quantity = forms.DecimalField(max_digits=12, decimal_places=3, min_value=Decimal("0.001"))
    unit_price_ht = forms.DecimalField(max_digits=12, decimal_places=0, min_value=Decimal("0"))
    tva_rate = forms.DecimalField(max_digits=5, decimal_places=2, min_value=Decimal("0"), initial=Decimal("0"))

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        if compte is not None:
            self.fields["product"].queryset = Product.objects.filter(compte=compte, is_active=True)


InvoiceLineFormSet = formset_factory(InvoiceLineForm, extra=3, can_delete=True)


class PaymentForm(BootstrapFormMixin, forms.Form):
    amount = forms.DecimalField(max_digits=14, decimal_places=0, min_value=Decimal("1"))
    method = forms.ChoiceField(choices=Payment.METHOD_CHOICES, initial=Payment.ESPECES)
    reference = forms.CharField(max_length=100, required=False)
