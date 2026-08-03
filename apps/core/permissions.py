from functools import wraps

from django.core.exceptions import PermissionDenied


def boutique_role_required(*roles):
    """Vérifie que l'utilisateur a un Membership actif avec l'un des rôles
    donnés sur la boutique courante (request.boutique, posée par
    CurrentTenantMiddleware)."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if request.boutique is None:
                raise PermissionDenied("Aucune boutique sélectionnée.")

            has_role = request.boutique.memberships.filter(
                user=request.user, is_active=True, role__in=roles
            ).exists()
            if not has_role:
                raise PermissionDenied("Rôle insuffisant pour cette boutique.")

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
            raise PermissionDenied("Réservé aux administrateurs de l'entreprise.")
        return view_func(request, *args, **kwargs)

    return wrapped
