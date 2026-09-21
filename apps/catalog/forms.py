from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin

from .models import Category, Product, Unit

MAX_PRODUCT_IMAGES = 3


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True

    def value_from_datadict(self, data, files, name):
        if hasattr(files, "getlist"):
            return files.getlist(name)
        return files.get(name)


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        data = data or []
        return [super(MultipleFileField, self).clean(item, initial) for item in data]


class ProductForm(BootstrapFormMixin, forms.ModelForm):
    extra_images = MultipleFileField(
        label=_("Photos supplémentaires"),
        required=False,
    )
    # Uniquement à la création (voir include_quantity) — modifier le stock
    # d'un produit existant passe par le module Mouvements de stock, pas
    # par ce formulaire.
    initial_quantity = forms.DecimalField(
        label=_("Quantité en stock"),
        max_digits=12, decimal_places=3, min_value=Decimal("0"),
        required=False, initial=0,
        help_text=_("Quantité disponible dès la création du produit (laisser à 0 si aucun stock pour l'instant)."),
    )

    class Meta:
        model = Product
        fields = [
            "name",
            "image",
            "category",
            # "sku",
            "barcode",
            "unit",
            "default_sale_price",
            "purchase_price",
            # "tva_rate",
            # "low_stock_threshold_default",
            "expiry_date",
            "is_active",
        ]
        labels = {
            "name": _("Nom"),
            "image": _("Photo principale"),
            "category": _("Catégorie"),
            "barcode": _("Code-barres"),
            "unit": _("Unité"),
            "purchase_price": _("Prix d'achat (FCFA)"),
            "is_active": _("Actif"),
        }
        help_texts = {
            "purchase_price": _(
                "Sert à calculer votre marge. Mis à jour automatiquement à chaque réception de "
                "marchandises (prix moyen pondéré) ; vous pouvez le corriger ici. Laisser vide si inconnu."
            ),
            "barcode": _("Scannez le code imprimé sur le produit (ou saisissez-le). Laisser vide s'il n'en a pas."),
        }
        widgets = {
            "expiry_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, compte=None, include_quantity=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.compte = compte
        self.fields["extra_images"].help_text = _(
            "Jusqu'à %(max)s photos au total par produit (photo principale incluse)."
        ) % {"max": MAX_PRODUCT_IMAGES}
        if compte is not None:
            self.fields["category"].queryset = Category.objects.filter(compte=compte)
            self.fields["unit"].queryset = Unit.objects.filter(compte=compte)
        if not include_quantity:
            del self.fields["initial_quantity"]
        else:
            self.order_fields(
                ["name", "image", "category", "barcode", "unit", "default_sale_price", "purchase_price",
                 "initial_quantity", "expiry_date", "is_active"]
            )

    def clean_barcode(self):
        """Un code-barres identifie UN produit : sans cette unicité (au sein
        de l'entreprise, le catalogue étant partagé entre ses boutiques), un
        scan à la caisse ajouterait au hasard l'un des deux. Espaces et
        retours à la ligne retirés : un lecteur ou un copier-coller en
        ajoute volontiers."""
        code = (self.cleaned_data.get("barcode") or "").strip()
        if not code:
            return ""
        clash = Product.objects.filter(compte=self.compte, barcode__iexact=code)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        other = clash.first()
        if other is not None:
            raise forms.ValidationError(
                _("Ce code-barres est déjà utilisé par le produit « %(name)s ».") % {"name": other.name}
            )
        return code

    def clean_initial_quantity(self):
        return self.cleaned_data.get("initial_quantity") or Decimal("0")

    def clean_extra_images(self):
        files = self.cleaned_data.get("extra_images") or []
        has_cover = bool(self.files.get("image")) or bool(self.instance.pk and self.instance.image)
        existing_extra_count = self.instance.extra_images.count() if self.instance.pk else 0
        total = (1 if has_cover else 0) + existing_extra_count + len(files)
        if total > MAX_PRODUCT_IMAGES:
            raise forms.ValidationError(
                f"Maximum {MAX_PRODUCT_IMAGES} photos par produit (actuellement {total})."
            )
        return files

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.compte = self.compte
        if commit:
            instance.save()
        return instance


class CategoryForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "parent"]
        labels = {"name": _("Nom"), "parent": _("Catégorie parente")}

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.compte = compte
        if compte is not None:
            self.fields["parent"].queryset = Category.objects.filter(compte=compte)
            if self.instance.pk:
                self.fields["parent"].queryset = self.fields["parent"].queryset.exclude(pk=self.instance.pk)

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.compte = self.compte
        if commit:
            instance.save()
        return instance


class UnitForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["name", "symbol"]
        labels = {"name": _("Nom"), "symbol": _("Symbole")}

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.compte = compte

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.compte = self.compte
        if commit:
            instance.save()
        return instance
