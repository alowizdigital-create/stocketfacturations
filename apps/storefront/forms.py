import re

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.sales.models import Invoice


class CheckoutForm(forms.Form):
    name = forms.CharField(label=_("Nom / prénom"), max_length=255)
    phone = forms.CharField(label=_("N° tel"), max_length=30)
    address = forms.CharField(label=_("Ville / quartier"), max_length=255)
    confirm = forms.BooleanField(
        label=_("Oui, je veux vraiment être livré(e)"),
        error_messages={"required": _("Confirmez que vous voulez vraiment être livré(e).")},
    )
    zone = forms.ChoiceField(label=_("Ville de livraison"), choices=[], required=False)
    note = forms.CharField(label=_("Message pour le vendeur (facultatif)"), max_length=300, required=False)
    payment_mode = forms.ChoiceField(label=_("Paiement"), widget=forms.RadioSelect, choices=[])
    payment_reference = forms.CharField(
        label=_("Référence de la transaction Mobile Money"), max_length=100, required=False,
    )
    # Piège à robots : un humain ne voit pas ce champ.
    website = forms.CharField(required=False, widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}))

    def __init__(self, *args, mobile_money_available=False, zones=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.zones = {str(z.id): z for z in zones}
        if zones:
            self.fields["zone"].choices = [("", _("— Choisissez votre ville —"))] + [(str(z.id), z.city) for z in zones]
            self.fields["zone"].required = True
        else:
            del self.fields["zone"]
        choices = [(Invoice.PAY_LIVRAISON, _("Payer à la livraison"))]
        if mobile_money_available:
            choices.append((Invoice.PAY_MOBILE_MONEY, _("Payer par Mobile Money (OM / MoMo)")))
        self.fields["payment_mode"].choices = choices
        self.fields["payment_mode"].initial = Invoice.PAY_LIVRAISON

    def clean_phone(self):
        phone = self.cleaned_data["phone"].strip()
        digits = re.sub(r"\D", "", phone)
        if len(digits) < 8:
            raise forms.ValidationError(_("Numéro de téléphone invalide."))
        return phone

    def clean_website(self):
        if self.cleaned_data.get("website"):
            raise forms.ValidationError("spam")
        return ""

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("payment_mode") == Invoice.PAY_MOBILE_MONEY and not cleaned.get("payment_reference", "").strip():
            self.add_error("payment_reference", _("Indiquez la référence reçue après votre paiement."))
        return cleaned
