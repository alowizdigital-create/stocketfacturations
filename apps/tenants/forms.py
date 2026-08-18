from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _

from apps.core.currencies import CURRENCY_CHOICES
from apps.core.forms import BootstrapFormMixin

from .countries import AFRICAN_COUNTRIES
from .models import Boutique, Compte, ExchangeRate, Membership, Plan, Subscription

User = get_user_model()


class SignupForm(BootstrapFormMixin, forms.Form):
    entreprise_name = forms.CharField(label=_("Nom de l'entreprise"), max_length=255)
    boutique_name = forms.CharField(label=_("Nom de la première boutique"), max_length=255)
    # boutique_code = forms.CharField(
    #     label="Code de la boutique",
    #     max_length=10,
    #     help_text="Ex: BTQ-001. Sert à numéroter les factures.",
    # )
    devise = forms.ChoiceField(
        label=_("Devise de la boutique"),
        choices=CURRENCY_CHOICES,
        initial="XOF",
        help_text=_("Toutes les ventes et devis de cette boutique utiliseront cette devise par défaut."),
    )
    email = forms.EmailField(label=_("Votre email"))
    # first_name = forms.CharField(label="Prénom", max_length=150, required=False)
    # last_name = forms.CharField(label="Nom", max_length=150, required=False)
    password = forms.CharField(label=_("Mot de passe"), widget=forms.PasswordInput)
    password_confirm = forms.CharField(label=_("Confirmer le mot de passe"), widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(_("Un compte existe déjà avec cet email."))
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("password_confirm"):
            if cleaned["password"] != cleaned["password_confirm"]:
                self.add_error("password_confirm", _("Les mots de passe ne correspondent pas."))
        return cleaned


class StaffCreateForm(BootstrapFormMixin, forms.Form):
    """Créée par un administrateur d'entreprise pour donner accès à un
    nouvel employé : crée le compte utilisateur et l'affecte directement à
    une boutique avec un rôle."""

    first_name = forms.CharField(label=_("Prénom"), max_length=150)
    last_name = forms.CharField(label=_("Nom"), max_length=150, required=False)
    email = forms.EmailField(label=_("Email de l'employé"))
    boutique = forms.ModelChoiceField(queryset=Boutique.objects.none(), label=_("Boutique"))
    role = forms.ChoiceField(choices=Membership.ROLE_CHOICES, label=_("Rôle"))
    password = forms.CharField(label=_("Mot de passe initial"), widget=forms.PasswordInput)
    password_confirm = forms.CharField(label=_("Confirmer le mot de passe"), widget=forms.PasswordInput)

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.compte = compte
        if compte is not None:
            self.fields["boutique"].queryset = Boutique.objects.filter(compte=compte, is_active=True)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(_("Un compte existe déjà avec cet email."))
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("password_confirm"):
            if cleaned["password"] != cleaned["password_confirm"]:
                self.add_error("password_confirm", _("Les mots de passe ne correspondent pas."))
        return cleaned


class StaffUpdateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Membership
        fields = ["boutique", "role", "is_active"]
        labels = {"boutique": _("Boutique"), "role": _("Rôle"), "is_active": _("Actif")}

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        if compte is not None:
            self.fields["boutique"].queryset = Boutique.objects.filter(compte=compte, is_active=True)


class CompteSettingsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compte
        fields = ["name", "email", "phone", "logo"]
        labels = {
            "name": _("Nom de l'entreprise"),
            "email": "Email",
            "phone": _("Téléphone"),
            "logo": _("Logo"),
        }


class BoutiqueRegionalForm(BootstrapFormMixin, forms.ModelForm):
    """Devise et pays de la boutique courante — permet à chaque boutique
    de fonctionner correctement quel que soit le pays d'Afrique où
    l'entreprise opère (montants affichés dans la bonne devise, numéros
    WhatsApp complétés avec le bon indicatif)."""

    devise = forms.ChoiceField(label=_("Devise"), choices=CURRENCY_CHOICES)
    country_calling_code = forms.ChoiceField(
        label=_("Pays (indicatif téléphonique)"),
        choices=[("", _("Non défini — numéros saisis tels quels"))] + AFRICAN_COUNTRIES,
        required=False,
    )

    class Meta:
        model = Boutique
        fields = [
            "devise",
            "country_calling_code",
            "om_account_name",
            "om_number",
            "momo_account_name",
            "momo_number",
        ]
        labels = {
            "devise": _("Devise"),
            "om_account_name": _("Orange Money — nom du compte"),
            "om_number": _("Orange Money — numéro"),
            "momo_account_name": _("MTN MoMo — nom du compte"),
            "momo_number": _("MTN MoMo — numéro"),
        }

    def clean_devise(self):
        return self.cleaned_data["devise"].strip().upper()


class ExchangeRateForm(BootstrapFormMixin, forms.ModelForm):
    """Ajout d'un taux de change pour la boutique courante — voir
    Boutique.exchange_rate_map, utilisé pour convertir les montants d'une
    vente/d'un devis créé dans une devise différente de celle de la
    boutique."""

    currency = forms.ChoiceField(label=_("Devise"))

    class Meta:
        model = ExchangeRate
        fields = ["currency", "rate"]
        labels = {"rate": _("1 unité de cette devise = ? dans la devise de la boutique")}

    def __init__(self, *args, boutique=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.boutique = boutique
        if boutique is not None:
            already_configured = set(boutique.exchange_rates.values_list("currency", flat=True))
            excluded = already_configured | {boutique.devise}
            self.fields["currency"].choices = [
                (code, label) for code, label in CURRENCY_CHOICES if code not in excluded
            ]

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.boutique = self.boutique
        if commit:
            instance.save()
        return instance


class SubscriptionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Subscription
        fields = ["plan", "expires_at", "payment_reference", "notes"]
        widgets = {"expires_at": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "plan": _("Offre"),
            "expires_at": _("Abonnement payé jusqu'au"),
            "payment_reference": _("Référence de paiement"),
            "notes": _("Notes"),
        }
        help_texts = {
            "expires_at": _("Laisser vide pour l'offre Gratuite. À mettre à jour vous-même après chaque paiement (Mobile Money, virement...)."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = Plan.objects.filter(is_active=True)
