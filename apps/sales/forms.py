from decimal import Decimal

from django import forms
from django.forms import formset_factory

from apps.catalog.models import Product
from apps.core.currencies import CURRENCY_CHOICES
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


def _currency_choices_for_boutique(boutique):
    """Seules la devise de la boutique et les devises pour lesquelles un
    taux de change est configuré (voir Boutique.exchange_rate_map) peuvent
    être proposées : sans taux, impossible de convertir correctement les
    montants d'une vente/d'un devis dans une autre devise."""
    if boutique is None:
        return CURRENCY_CHOICES
    label_by_code = dict(CURRENCY_CHOICES)
    codes = list(boutique.exchange_rate_map.keys())
    codes.sort(key=lambda code: (code != boutique.devise, code))
    choices = []
    for code in codes:
        label = label_by_code.get(code, code)
        if code == boutique.devise:
            label += " — devise de la boutique"
        choices.append((code, label))
    return choices


class InvoiceForm(BootstrapFormMixin, forms.Form):
    client = forms.ModelChoiceField(queryset=Client.objects.none(), required=False, label="Client")
    currency = forms.ChoiceField(
        label="Devise",
        required=False,
        help_text="Devise de ce document — par défaut celle de la boutique, modifiable au cas par cas.",
    )
    discount_amount = forms.IntegerField(
        label="Remise globale (FCFA)", min_value=0, required=False, initial=0,
    )
    # N'est affiché dans le template que pour un devis (voir
    # document_kind dans invoice_form.html) — une commande/facture garde
    # le format ticket 80mm par défaut, non demandé ici.
    pdf_format = forms.ChoiceField(
        choices=Invoice.PDF_FORMAT_CHOICES, initial=Invoice.PDF_FORMAT_80MM,
        required=False, label="Format du PDF",
    )
    # Ces deux champs ne concernent qu'une commande (voir invoice_form.html)
    # — un devis n'a ni acompte ni note interne dans le périmètre actuel.
    # Renseignés via le pop-up affiché au clic sur "Créer la commande"
    # (même principe que payment_type/deposit_amount sur SaleForm), jamais
    # affichés directement comme champs de formulaire classiques.
    note = forms.CharField(required=False, widget=forms.HiddenInput())
    deposit_amount = forms.DecimalField(
        max_digits=14, decimal_places=0, min_value=Decimal("0"),
        widget=forms.HiddenInput(), required=False,
    )

    def __init__(self, *args, boutique=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].choices = _currency_choices_for_boutique(boutique)
        if boutique is not None:
            self.fields["client"].queryset = Client.objects.filter(boutique=boutique).order_by("name")
            self.fields["currency"].initial = boutique.devise

    def clean_currency(self):
        return self.cleaned_data.get("currency") or ""

    def clean_discount_amount(self):
        return self.cleaned_data.get("discount_amount") or 0

    def clean_deposit_amount(self):
        return self.cleaned_data.get("deposit_amount") or Decimal("0")

    def clean_pdf_format(self):
        return self.cleaned_data.get("pdf_format") or Invoice.PDF_FORMAT_80MM


class SaleForm(BootstrapFormMixin, forms.Form):
    # Champ caché : la sélection se fait via la recherche par nom en JS
    # (voir sale_form.html), qui écrit l'id du client choisi ici.
    client = forms.ModelChoiceField(
        queryset=Client.objects.none(), required=False, label="Client", widget=forms.HiddenInput()
    )
    currency = forms.ChoiceField(
        label="Devise",
        required=False,
        help_text="Devise de cette vente — par défaut celle de la boutique, modifiable au cas par cas.",
    )
    # Renseignés par le pop-up de paiement affiché au clic sur "Enregistrer
    # la vente" (voir sale_form.html) : paiement complet ou acompte.
    payment_type = forms.ChoiceField(
        choices=[("full", "Payé en totalité"), ("partial", "Acompte")],
        widget=forms.HiddenInput(), initial="full", required=False,
    )
    deposit_amount = forms.DecimalField(
        max_digits=14, decimal_places=0, min_value=Decimal("0"),
        widget=forms.HiddenInput(), required=False,
    )

    def __init__(self, *args, boutique=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency"].choices = _currency_choices_for_boutique(boutique)
        if boutique is not None:
            self.fields["client"].queryset = Client.objects.filter(boutique=boutique).order_by("name")
            self.fields["currency"].initial = boutique.devise

    def clean_currency(self):
        return self.cleaned_data.get("currency") or ""

    def clean_deposit_amount(self):
        return self.cleaned_data.get("deposit_amount") or Decimal("0")


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
    # Pas de valeur initiale non-None : avec initial=0, Django considère à
    # tort qu'une ligne vide non soumise (champ retiré de l'affichage) "a
    # changé" (bug connu de Field.has_changed avec un initial falsy mais
    # non-None face à une valeur absente), ce qui bloquait la validation de
    # toute ligne vide restante en fin de tableau.
    discount_amount = forms.IntegerField(
        label="Remise (FCFA)", min_value=0, required=False,
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
