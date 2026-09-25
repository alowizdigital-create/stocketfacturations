from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from apps.core.currencies import CURRENCY_CHOICES
from apps.core.forms import BootstrapFormMixin

User = get_user_model()


class GoogleSignupConfirmForm(BootstrapFormMixin, forms.Form):
    """Dernière étape avant de créer l'entreprise d'un compte arrivé par
    Google (voir apps.accounts.views.google_signup_confirm) — l'email est
    déjà vérifié par Google à ce stade, on ne redemande que ce que Google ne
    sait pas : le nom de l'entreprise et de sa première boutique."""

    entreprise_name = forms.CharField(label=_("Nom de l'entreprise"), max_length=255)
    boutique_name = forms.CharField(label=_("Nom de la première boutique"), max_length=255)
    devise = forms.ChoiceField(
        label=_("Devise de la boutique"), choices=CURRENCY_CHOICES, initial="XOF",
        help_text=_("Toutes les ventes et devis de cette boutique utiliseront cette devise par défaut."),
    )

    def __init__(self, *args, email=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.email = email

    def clean(self):
        cleaned = super().clean()
        # Revérifié ici (déjà fait juste avant la redirection vers ce
        # formulaire) : entre-temps, ce même compte Google a pu créer son
        # entreprise dans un autre onglet.
        if self.email and User.objects.filter(email=self.email).exists():
            raise forms.ValidationError(
                _("Un compte existe déjà avec l'email %(email)s.") % {"email": self.email}
            )
        return cleaned
