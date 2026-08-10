from django.utils import timezone
from rest_framework import authentication, exceptions

from apps.tenants.models import BoutiqueAPIToken


class BoutiquePrincipal:
    """Se substitue à `request.user` pour une requête authentifiée par
    jeton boutique — compatible avec `IsAuthenticated` (DRF vérifie
    `request.user.is_authenticated`), sans être un vrai `User` Django."""

    is_authenticated = True
    is_anonymous = False

    def __init__(self, boutique):
        self.boutique = boutique
        self.compte = boutique.compte

    def __str__(self):
        return f"boutique:{self.boutique.id}"


class BoutiqueTokenAuthentication(authentication.BaseAuthentication):
    """Authentifie une requête de synchro par le jeton de la boutique
    (Authorization: Bearer <token>). La boutique n'est jamais déduite du
    corps de la requête — toujours de ce jeton."""

    keyword = "Bearer"

    def authenticate(self, request):
        auth_header = authentication.get_authorization_header(request).decode("utf-8")
        if not auth_header or not auth_header.startswith(f"{self.keyword} "):
            return None

        raw_token = auth_header[len(self.keyword) + 1:]
        token = BoutiqueAPIToken.resolve(raw_token)
        if token is None:
            raise exceptions.AuthenticationFailed("Jeton boutique invalide.")

        BoutiqueAPIToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        return (BoutiquePrincipal(token.boutique), token)

    def authenticate_header(self, request):
        # Sans ceci, DRF renvoie 403 (Forbidden) au lieu de 401
        # (Unauthorized) quand aucun jeton n'est fourni.
        return self.keyword
