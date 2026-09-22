"""Limites du plan d'abonnement d'une entreprise (nombre de boutiques et
d'employés) — voir apps.tenants.models.Plan/Subscription. Une entreprise
sans abonnement (ne devrait pas arriver après l'inscription, voir
tenants.views.signup, mais possible pour un compte créé autrement, ex:
admin Django) n'est pas limitée : on refuse par prudence plutôt que de
bloquer une entreprise sans qu'on sache pourquoi."""

from .models import Boutique, Membership


def _plan(compte):
    subscription = getattr(compte, "subscription", None)
    return subscription.plan if subscription is not None else None


def boutiques_limit_reached(compte):
    """True si l'entreprise a atteint le nombre de boutiques actives permis
    par son offre (limite vide = illimité)."""
    plan = _plan(compte)
    if plan is None or plan.max_boutiques is None:
        return False
    count = Boutique.objects.filter(compte=compte, is_active=True).count()
    return count >= plan.max_boutiques


def users_limit_reached(compte):
    """True si l'entreprise a atteint le nombre d'employés (comptes
    utilisateurs distincts ayant un accès actif à l'une de ses boutiques)
    permis par son offre. Un même utilisateur affecté à plusieurs boutiques
    de l'entreprise ne compte qu'une fois."""
    plan = _plan(compte)
    if plan is None or plan.max_users is None:
        return False
    count = (
        Membership.objects.filter(boutique__compte=compte, is_active=True)
        .values("user_id").distinct().count()
    )
    return count >= plan.max_users
