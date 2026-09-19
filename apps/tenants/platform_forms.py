from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.utils.translation import gettext_lazy as _

from apps.core.forms import BootstrapFormMixin

User = get_user_model()


class PlatformUserForm(BootstrapFormMixin, forms.ModelForm):
    """Informations d'un utilisateur, modifiables par le super-admin de la
    plateforme. Volontairement absent : is_superuser/is_staff/permissions
    (donner le contrôle de la plateforme reste une opération de l'admin
    Django, pas de cet écran) et le mot de passe (voir PlatformPasswordForm)."""

    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "phone", "is_active"]
        labels = {
            "email": _("Adresse e-mail (identifiant de connexion)"),
            "first_name": _("Prénom"),
            "last_name": _("Nom"),
            "phone": _("Téléphone"),
            "is_active": _("Compte actif (peut se connecter)"),
        }

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(_("Un autre compte utilise déjà cet e-mail."))
        return email


class PlatformPasswordForm(BootstrapFormMixin, forms.Form):
    """Nouveau mot de passe défini par le super-admin (l'ancien n'est
    jamais lisible : seul un hash est stocké). Soumis aux mêmes règles de
    robustesse que partout ailleurs dans l'application."""

    new_password1 = forms.CharField(label=_("Nouveau mot de passe"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    new_password2 = forms.CharField(label=_("Confirmer le mot de passe"), widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        cleaned = super().clean()
        password1 = cleaned.get("new_password1")
        password2 = cleaned.get("new_password2")
        if password1 and password2:
            if password1 != password2:
                self.add_error("new_password2", _("Les mots de passe ne correspondent pas."))
            else:
                try:
                    validate_password(password1, user=self.user)
                except forms.ValidationError as exc:
                    self.add_error("new_password1", exc)
        return cleaned
