from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin


class ActivationForm(BootstrapFormMixin, forms.Form):
    token = forms.CharField(
        label=_("Jeton d'activation"),
        help_text=_("Généré depuis les paramètres de la boutique (Synchronisation hors-ligne) sur le poste en ligne."),
        widget=forms.Textarea(attrs={"rows": 2}),
    )
