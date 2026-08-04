from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from apps.core.forms import BootstrapFormMixin

from .models import Boutique, Compte, Membership, Plan, Subscription

User = get_user_model()


class SignupForm(BootstrapFormMixin, forms.Form):
    entreprise_name = forms.CharField(label="Nom de l'entreprise", max_length=255)
    boutique_name = forms.CharField(label="Nom de la première boutique", max_length=255)
    boutique_code = forms.CharField(
        label="Code de la boutique",
        max_length=10,
        help_text="Ex: BTQ-001. Sert à numéroter les factures.",
    )
    email = forms.EmailField(label="Votre email")
    first_name = forms.CharField(label="Prénom", max_length=150, required=False)
    last_name = forms.CharField(label="Nom", max_length=150, required=False)
    password = forms.CharField(label="Mot de passe", widget=forms.PasswordInput)
    password_confirm = forms.CharField(label="Confirmer le mot de passe", widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cet email.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("password_confirm"):
            if cleaned["password"] != cleaned["password_confirm"]:
                self.add_error("password_confirm", "Les mots de passe ne correspondent pas.")
        return cleaned


class StaffCreateForm(BootstrapFormMixin, forms.Form):
    """Créée par un administrateur d'entreprise pour donner accès à un
    nouvel employé : crée le compte utilisateur et l'affecte directement à
    une boutique avec un rôle."""

    email = forms.EmailField(label="Email de l'employé")
    first_name = forms.CharField(label="Prénom", max_length=150, required=False)
    last_name = forms.CharField(label="Nom", max_length=150, required=False)
    boutique = forms.ModelChoiceField(queryset=Boutique.objects.none(), label="Boutique")
    role = forms.ChoiceField(choices=Membership.ROLE_CHOICES, label="Rôle")
    password = forms.CharField(label="Mot de passe initial", widget=forms.PasswordInput)
    password_confirm = forms.CharField(label="Confirmer le mot de passe", widget=forms.PasswordInput)

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.compte = compte
        if compte is not None:
            self.fields["boutique"].queryset = Boutique.objects.filter(compte=compte, is_active=True)

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cet email.")
        return email

    def clean_password(self):
        password = self.cleaned_data["password"]
        validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") and cleaned.get("password_confirm"):
            if cleaned["password"] != cleaned["password_confirm"]:
                self.add_error("password_confirm", "Les mots de passe ne correspondent pas.")
        return cleaned


class StaffUpdateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Membership
        fields = ["boutique", "role", "is_active"]
        labels = {"boutique": "Boutique", "role": "Rôle", "is_active": "Actif"}

    def __init__(self, *args, compte=None, **kwargs):
        super().__init__(*args, **kwargs)
        if compte is not None:
            self.fields["boutique"].queryset = Boutique.objects.filter(compte=compte, is_active=True)


class CompteSettingsForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compte
        fields = ["name", "email", "phone", "logo"]
        labels = {
            "name": "Nom de l'entreprise",
            "email": "Email",
            "phone": "Téléphone",
            "logo": "Logo",
        }


class SubscriptionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Subscription
        fields = ["plan", "expires_at", "payment_reference", "notes"]
        widgets = {"expires_at": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "plan": "Offre",
            "expires_at": "Abonnement payé jusqu'au",
            "payment_reference": "Référence de paiement",
            "notes": "Notes",
        }
        help_texts = {
            "expires_at": "Laisser vide pour l'offre Gratuite. À mettre à jour vous-même après chaque paiement (Mobile Money, virement...).",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["plan"].queryset = Plan.objects.filter(is_active=True)
