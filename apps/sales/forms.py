from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.catalog.models import Product
from apps.core.forms import BootstrapFormMixin

from .models import Client, Invoice, Payment
 

class ClientForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name", "phone", "email", "address"]


class ClientImportForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField(
        label="Fichier CSV ou vCard (.vcf)",
        help_text=(
            "CSV : colonnes nom, téléphone, email, adresse (name/phone/email/address acceptés aussi). "
            "vCard : export standard d'un carnet de contacts téléphone/Google/Outlook. "
            "Seul le nom est obligatoire."
        ),
        widget=forms.ClearableFileInput(attrs={"accept": ".csv,.vcf,text/csv,text/vcard"}),
    )

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        name = (uploaded.name or "").lower()
        if not (name.endswith(".csv") or name.endswith(".vcf") or name.endswith(".txt")):
            raise forms.ValidationError("Format non reconnu : utilisez un fichier .csv ou .vcf.")
        return uploaded


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
    # La sélection se fait via la recherche en direct en JS (même principe
    # que l'écran de vente) : ce champ ne porte que l'id du produit choisi.
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(), required=False, label="Produit",
        widget=forms.HiddenInput(),
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
            self.fields["product"].queryset = Product.objects.filter(compte=compte, is_active=True)

    def clean_discount_amount(self):
        return self.cleaned_data.get("discount_amount") or 0


InvoiceLineFormSet = formset_factory(InvoiceLineForm, extra=1, can_delete=True)


class PaymentForm(BootstrapFormMixin, forms.Form):
    amount = forms.DecimalField(max_digits=14, decimal_places=0, min_value=Decimal("1"))
    method = forms.ChoiceField(choices=Payment.METHOD_CHOICES, initial=Payment.ESPECES)
    reference = forms.CharField(max_length=100, required=False)
