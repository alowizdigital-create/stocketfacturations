from django import forms
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin

from .models import CashMovement

User = get_user_model()


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


class CashTransferForm(BootstrapFormMixin, forms.Form):
    """Transfert depuis la caisse individuelle de l'utilisateur courant
    vers celle d'un collègue de la même boutique — voir
    services.transfer_cash. La liste des destinataires se limite aux
    autres membres actifs de la boutique (pas de transfert vers quelqu'un
    qui n'y a pas accès)."""

    to_user = forms.ModelChoiceField(queryset=User.objects.none(), label=_("Vers"))
    amount = forms.DecimalField(label=_("Montant"), min_value=1, max_digits=14, decimal_places=0)
    reason = forms.CharField(label=_("Motif (optionnel)"), max_length=255, required=False)

    def __init__(self, *args, boutique=None, exclude_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if boutique is not None:
            queryset = User.objects.filter(
                memberships__boutique=boutique, memberships__is_active=True
            ).distinct().order_by("email")
            if exclude_user is not None:
                queryset = queryset.exclude(pk=exclude_user.pk)
            self.fields["to_user"].queryset = queryset
