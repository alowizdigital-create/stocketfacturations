from django import forms

from apps.core.forms import BootstrapFormMixin


class ActivationForm(BootstrapFormMixin, forms.Form):
    token = forms.CharField(
        label="Jeton d'activation",
        help_text="Généré depuis les paramètres de la boutique (Synchronisation hors-ligne) sur le poste en ligne.",
        widget=forms.Textarea(attrs={"rows": 2}),
    )
