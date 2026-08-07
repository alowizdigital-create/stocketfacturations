from django import forms

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
        label="Photos supplémentaires",
        required=False,
        help_text=f"Jusqu'à {MAX_PRODUCT_IMAGES} photos au total par produit (photo principale incluse).",
    )

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
            "image": "Photo principale",
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
