from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect


def _redirect_back(request):
    """Renvoie l'utilisateur d'où il vient (la page depuis laquelle il a
    tenté l'action refusée) plutôt qu'une page d'erreur brute — le message
    rouge affiché juste avant l'explique déjà, pas besoin d'écran à part."""
    return redirect(request.META.get("HTTP_REFERER") or "core:home")


def boutique_role_required(*roles):
    """Vérifie que l'utilisateur a un Membership actif avec l'un des rôles
    donnés sur la boutique courante (request.boutique, posée par
    CurrentTenantMiddleware). Refus = message rouge + retour à la page
    précédente, sur le même principe que les messages de succès — pas une
    page d'erreur brute (403) qui ne dit pas à l'utilisateur pourquoi."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if request.boutique is None:
                messages.error(request, "Aucune boutique sélectionnée.")
                return _redirect_back(request)

            has_role = request.boutique.memberships.filter(
                user=request.user, is_active=True, role__in=roles
            ).exists()
            if not has_role:
                messages.error(request, "Vous n'avez pas les droits nécessaires pour effectuer cette action.")
                return _redirect_back(request)

            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def block_when_offline(redirect_url_name):
    """Bloque une vue de gestion du catalogue (produits/catégories/unités
    — "pull-only" côté offline, voir settings.IS_OFFLINE) : un changement
    fait sur un poste offline n'est jamais poussé vers le serveur, il
    serait donc perdu silencieusement au prochain pull sans ce garde-fou.
    Message + redirection plutôt qu'une erreur d'accès, puisque ce n'est
    pas un problème de droits mais d'endroit où gérer le catalogue."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if settings.IS_OFFLINE:
                messages.error(
                    request,
                    "Le catalogue (produits, catégories, unités) se gère uniquement "
                    "depuis le poste en ligne — les changements faits ici ne seraient "
                    "jamais synchronisés.",
                )
                return redirect(redirect_url_name)
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


def compte_admin_required(view_func):
    """Vérifie que l'utilisateur est administrateur de l'entreprise
    courante (request.compte) — utilisé pour les actions qui portent sur
    toute l'entreprise plutôt que sur une seule boutique (ex: gestion du
    personnel)."""

    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.is_compte_admin:
            messages.error(request, "Cette action est réservée aux administrateurs de l'entreprise.")
            return _redirect_back(request)
        return view_func(request, *args, **kwargs)

    return wrapped
