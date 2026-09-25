"""Création d'une entreprise avec sa première boutique — partagé par
l'inscription classique (apps.tenants.views.signup, email + mot de passe) et
l'inscription via Google (apps.accounts.views.google_signup_confirm), qui
doivent aboutir exactement au même état : une entreprise, une boutique par
défaut, une unité "Pièce", l'offre Gratuite si elle existe, et un
administrateur."""

from django.db import transaction

from apps.catalog.models import Unit

from .models import Boutique, Compte, Membership, Plan, Subscription


@transaction.atomic
def create_company_with_owner(*, user_model, entreprise_name, boutique_name, devise, email, password=None):
    """Crée l'entreprise, sa boutique par défaut et son administrateur.
    `password=None` laisse un mot de passe inutilisable (voir
    UserManager.create_user / AbstractBaseUser.set_password) — le cas d'un
    compte créé via Google, qui pourra en définir un plus tard via "mot de
    passe oublié" s'il veut aussi se connecter depuis le poste offline.
    Renvoie (user, boutique)."""

    entreprise_name = entreprise_name.strip()
    compte = Compte.objects.create(name=entreprise_name, email=email)

    # Chaque entreprise démarre avec une boutique par défaut, sélectionnée
    # automatiquement à la connexion (voir CurrentTenantMiddleware) — pas
    # besoin de choisir tant qu'il n'y en a qu'une.
    boutique_code = entreprise_name.replace(" ", "")[:3].upper()
    boutique = Boutique.objects.create(
        compte=compte, name=boutique_name, code=boutique_code, devise=devise, is_default=True,
    )
    Unit.objects.create(compte=compte, name="Pièce", symbol="pc")
    free_plan = Plan.objects.filter(name="Gratuit").first()
    if free_plan:
        Subscription.objects.create(compte=compte, plan=free_plan)

    user = user_model.objects.create_user(email=email, password=password)
    Membership.objects.create(user=user, boutique=boutique, role=Membership.ADMIN_COMPTE)
    return user, boutique
