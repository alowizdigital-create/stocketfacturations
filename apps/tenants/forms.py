from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from apps.core.forms import BootstrapFormMixin

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
