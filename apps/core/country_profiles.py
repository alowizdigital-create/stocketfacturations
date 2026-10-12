"""Profils de départ par pays : devise, TVA standard et indicatif appliqués à
la première boutique à l'inscription (voir
apps.tenants.services.create_company_with_owner). Ce ne sont que des valeurs
initiales — tout reste modifiable ensuite dans Paramètres de l'entreprise."""

from decimal import Decimal

DEFAULT_COUNTRY = "CM"

COUNTRY_PROFILES = {
    "CM": {
        "label": "Cameroun",
        "devise": "XAF",
        "tva_rate": Decimal("19.25"),  # TVA 17,5 % + centimes additionnels 10 %
        "calling_code": "237",
    },
}

# ("", ...) = aucun profil : devise choisie à la main, TVA à 0, comme avant.
COUNTRY_CHOICES = [("CM", "Cameroun — XAF, TVA 19,25 %"), ("", "Autre pays (à régler à la main)")]


def apply_profile(country, devise):
    """Renvoie (devise, tva_rate, calling_code) pour la boutique à créer :
    le profil l'emporte sur la devise saisie, qui n'est utilisée que sans
    profil."""
    profile = COUNTRY_PROFILES.get(country or "")
    if profile is None:
        return devise, Decimal("0"), ""
    return profile["devise"], profile["tva_rate"], profile["calling_code"]
