from django.conf import settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect


class PasswordResetView(auth_views.PasswordResetView):
    """Bloquée hors-ligne : un mot de passe changé sur ce poste ne se
    synchroniserait jamais vers le serveur (PullUsersView est en lecture
    seule, pas de push pour User) — le compte se retrouverait avec un mot
    de passe différent en ligne et hors-ligne, sans aucun moyen de le
    corriger depuis l'app elle-même."""

    def dispatch(self, request, *args, **kwargs):
        if settings.IS_OFFLINE:
            messages.error(
                request,
                "La réinitialisation de mot de passe n'est disponible qu'en ligne — "
                "utilisez un poste connecté à internet.",
            )
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)
