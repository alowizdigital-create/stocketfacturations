from django import forms

from apps.core.forms import BootstrapFormMixin

from .models import Category, Product, Unit


class ProductForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "name",
            "image",
            "category",
            # "sku",
            # "barcode",
            "unit",
            "default_sale_price",
            # "tva_rate",
            # "low_stock_threshold_default",
            "is_active",
        ]
        labels = {
            "name": "Nom",
            "image": "Photo",
            "category": "Catégorie",
            "unit": "Unité",
            "is_active": "Actif",
        }

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.compte = compte
        if compte is not None:
            self.fields["category"].queryset = Category.objects.filter(compte=compte)
            self.fields["unit"].queryset = Unit.objects.filter(compte=compte)

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
        labels = {"name": "Nom", "parent": "Catégorie parente"}

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
        labels = {"name": "Nom", "symbol": "Symbole"}

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.compte = compte

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.compte = self.compte
        if commit:
            instance.save()
        return instance
