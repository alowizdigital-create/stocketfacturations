import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, views as auth_views
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _

from apps.tenants.services import create_company_with_owner

from . import google_oauth
from .forms import GoogleSignupConfirmForm

User = get_user_model()

# Clé de session portant l'anti-CSRF du flux Google (générée à
# google_login, vérifiée à google_callback) et celle portant l'email/nom
# vérifiés par Google en attendant la confirmation du nom de l'entreprise
# (voir google_signup_confirm) — jamais un mot de passe ni un jeton Google,
# rien de sensible n'y transite.
GOOGLE_OAUTH_STATE_SESSION_KEY = "google_oauth_state"
GOOGLE_PENDING_SIGNUP_SESSION_KEY = "google_pending_signup"


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
                _(
                    "La réinitialisation de mot de passe n'est disponible qu'en ligne — "
                    "utilisez un poste connecté à internet."
                ),
            )
            return redirect("accounts:login")
        return super().dispatch(request, *args, **kwargs)


def google_login(request):
    """Point de départ du bouton "Se connecter avec Google" : redirige vers
    l'écran de consentement Google. `state` (anti-CSRF standard du flux
    OAuth) est gardé en session le temps de l'aller-retour et revérifié à
    google_callback."""
    if settings.IS_OFFLINE or not google_oauth.is_configured():
        messages.error(request, _("La connexion Google n'est pas disponible ici."))
        return redirect("accounts:login")

    state = secrets.token_urlsafe(24)
    request.session[GOOGLE_OAUTH_STATE_SESSION_KEY] = state
    return redirect(google_oauth.build_authorization_url(request, state))


def google_callback(request):
    """Retour de Google après consentement — jamais atteint hors-ligne
    (google_login ne redirige jamais dans ce cas, mais le poste pourrait
    recevoir l'URL d'un lien partagé par erreur)."""
    if settings.IS_OFFLINE or not google_oauth.is_configured():
        messages.error(request, _("La connexion Google n'est pas disponible ici."))
        return redirect("accounts:login")

    expected_state = request.session.pop(GOOGLE_OAUTH_STATE_SESSION_KEY, None)
    state = request.GET.get("state")
    if not expected_state or state != expected_state:
        messages.error(request, _("La demande de connexion Google a expiré — réessayez."))
        return redirect("accounts:login")

    if request.GET.get("error"):
        # L'utilisateur a annulé sur l'écran Google, ou a refusé le partage
        # de son profil — pas une erreur applicative, retour silencieux.
        return redirect("accounts:login")

    code = request.GET.get("code")
    if not code:
        messages.error(request, _("Réponse de Google invalide — réessayez."))
        return redirect("accounts:login")

    try:
        token_data = google_oauth.exchange_code(request, code)
        userinfo = google_oauth.fetch_userinfo(token_data["access_token"])
    except google_oauth.GoogleAuthError as exc:
        messages.error(request, _("Échec de la connexion Google : %(error)s") % {"error": exc})
        return redirect("accounts:login")

    email = (userinfo.get("email") or "").lower().strip()
    # email_verified peut être un booléen ou la chaîne "true" selon
    # l'endpoint Google — on ne fait confiance qu'à un email que Google
    # affirme explicitement vérifié.
    email_verified = userinfo.get("email_verified") in (True, "true")
    if not email or not email_verified:
        messages.error(
            request, _("Votre compte Google doit avoir une adresse email vérifiée pour vous connecter ici.")
        )
        return redirect("accounts:login")

    existing = User.objects.filter(email=email).first()
    if existing is not None:
        login(request, existing, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("core:home")

    # Aucun compte Zweey pour cet email : dernière étape avant d'en créer un
    # (voir google_signup_confirm) — rien n'est créé tant qu'elle n'est pas
    # validée.
    request.session[GOOGLE_PENDING_SIGNUP_SESSION_KEY] = {
        "email": email, "name": userinfo.get("name", ""),
    }
    return redirect("accounts:google_signup_confirm")


def google_signup_confirm(request):
    """Dernière étape de l'inscription via Google : confirmer le nom de
    l'entreprise et de sa première boutique (l'email est déjà acquis, voir
    google_callback). Sans session en attente (accès direct à l'URL, lien
    partagé, session expirée), retour à la connexion plutôt qu'une page qui
    ne pourrait rien faire."""
    pending = request.session.get(GOOGLE_PENDING_SIGNUP_SESSION_KEY)
    if not pending:
        return redirect("accounts:login")

    if request.method == "POST":
        form = GoogleSignupConfirmForm(request.POST, email=pending["email"])
        if form.is_valid():
            data = form.cleaned_data
            user, boutique = create_company_with_owner(
                user_model=User,
                entreprise_name=data["entreprise_name"],
                boutique_name=data["boutique_name"],
                devise=data["devise"],
                email=pending["email"],
                password=None,
            )
            del request.session[GOOGLE_PENDING_SIGNUP_SESSION_KEY]
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session["boutique_id"] = str(boutique.id)
            messages.success(
                request,
                _(
                    "Bienvenue ! Votre entreprise est créée. Définissez un mot de passe (voir « Mot de passe "
                    "oublié ») si vous voulez aussi vous connecter depuis le poste hors-ligne."
                ),
            )
            return redirect("core:home")
    else:
        # Nom d'entreprise suggéré à partir du prénom Google, à corriger
        # librement — jamais imposé silencieusement.
        suggested = pending.get("name") or pending["email"].split("@")[0]
        form = GoogleSignupConfirmForm(
            email=pending["email"],
            initial={"entreprise_name": suggested, "boutique_name": _("Boutique principale")},
        )

    return render(request, "accounts/google_signup_confirm.html", {"form": form, "email": pending["email"]})
