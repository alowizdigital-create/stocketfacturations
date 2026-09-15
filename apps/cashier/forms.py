from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin

from .models import CashMovement


class CashSessionOpenForm(BootstrapFormMixin, forms.Form):
    opening_amount = forms.DecimalField(
        label=_("Fond de caisse (montant d'ouverture)"),
        min_value=0, max_digits=14, decimal_places=0,
    )


class CashMovementForm(BootstrapFormMixin, forms.Form):
    TYPE_CHOICES = [
        (CashMovement.ENTREE, _("Entrée")),
        (CashMovement.SORTIE, _("Sortie")),
    ]

    type = forms.ChoiceField(choices=TYPE_CHOICES, label=_("Type"), widget=forms.RadioSelect)
    amount = forms.DecimalField(label=_("Montant"), min_value=1, max_digits=14, decimal_places=0)
    # Motif obligatoire : contrairement au mouvement de stock (optionnel),
    # un mouvement d'argent manuel doit toujours être justifiable.
    reason = forms.CharField(label=_("Motif"), max_length=255)


class CashSessionCloseForm(BootstrapFormMixin, forms.Form):
    counted_amount = forms.DecimalField(
        label=_("Montant compté"), min_value=0, max_digits=14, decimal_places=0,
    )
    closing_note = forms.CharField(label=_("Note (optionnel)"), widget=forms.Textarea(attrs={"rows": 2}), required=False)
