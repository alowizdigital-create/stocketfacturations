from django import forms
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin

from .models import Expense, Supplier


class SupplierForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ["name", "phone", "email", "address", "notes", "is_active"]
        labels = {
            "name": _("Nom du fournisseur"), "phone": _("Téléphone"), "email": "Email",
            "address": _("Adresse"), "notes": _("Notes"), "is_active": _("Actif (proposé lors des réceptions)"),
        }
        widgets = {"notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.compte = compte

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        clash = Supplier.objects.filter(compte=self.compte, name__iexact=name)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(_("Un fournisseur porte déjà ce nom."))
        return name

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.compte = self.compte
        if commit:
            instance.save()
        return instance


class PurchaseHeaderForm(BootstrapFormMixin, forms.Form):
    """En-tête d'une réception. Les lignes (produits, quantités, coûts)
    arrivent à part, en JSON construit par l'écran (voir purchase_form.html)."""

    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none(), required=False, label=_("Fournisseur"))
    received_date = forms.DateField(
        label=_("Date de réception"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}),
    )
    reference = forms.CharField(
        label=_("Référence fournisseur"), max_length=100, required=False,
        help_text=_("N° de la facture ou du bon de livraison du fournisseur."),
    )
    note = forms.CharField(label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        if compte is not None:
            self.fields["supplier"].queryset = Supplier.objects.filter(compte=compte, is_active=True)

    def clean_received_date(self):
        received = self.cleaned_data["received_date"]
        if received > timezone.localdate():
            raise forms.ValidationError(_("La date de réception ne peut pas être dans le futur."))
        return received


class ExpenseForm(BootstrapFormMixin, forms.Form):
    category = forms.ChoiceField(label=_("Catégorie"), choices=Expense.CATEGORY_CHOICES)
    label = forms.CharField(label=_("Libellé"), max_length=255, help_text=_("Ex : loyer de septembre, carburant livraison..."))
    amount = forms.DecimalField(label=_("Montant"), max_digits=14, decimal_places=0, min_value=1)
    expense_date = forms.DateField(
        label=_("Date"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}),
    )
    note = forms.CharField(label=_("Note"), required=False, widget=forms.Textarea(attrs={"rows": 2}))
    from_personal_cash = forms.BooleanField(
        label=_("Payée avec l'argent de ma caisse"), required=False,
        help_text=_("Le montant sera déduit de votre caisse (Ma caisse)."),
    )

    def clean_expense_date(self):
        value = self.cleaned_data["expense_date"]
        if value > timezone.localdate():
            raise forms.ValidationError(_("La date ne peut pas être dans le futur."))
        return value
