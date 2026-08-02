class CurrentTenantMiddleware:
    """Résout request.compte et request.boutique à partir de la session de
    l'utilisateur connecté (mode en ligne). En mode offline, l'exe fixe ces
    valeurs une bonne fois pour toutes à l'activation — voir desktop/.

    Si aucune boutique n'est encore choisie en session, sélectionne
    automatiquement : la seule boutique active de l'utilisateur s'il n'en a
    qu'une, sinon sa boutique marquée "par défaut". Ce n'est que s'il a
    plusieurs boutiques actives sans défaut clair que l'écran de choix
    (tenants:choose_boutique) reste nécessaire.

    Ne fait aucune hypothèse sur les droits de l'utilisateur sur la
    boutique choisie : c'est aux vues/permissions de vérifier qu'il existe
    bien un Membership actif correspondant avant d'agir.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.compte = None
        request.boutique = None

        if request.user.is_authenticated:
            from apps.tenants.models import Boutique

            boutique = None
            boutique_id = request.session.get("boutique_id")
            if boutique_id:
                boutique = self._active_boutiques(request).filter(id=boutique_id).first()

            if boutique is None:
                boutique = self._auto_select(request)
                if boutique is not None:
                    request.session["boutique_id"] = str(boutique.id)

            if boutique is not None:
                request.boutique = boutique
                request.compte = boutique.compte

        return self.get_response(request)

    @staticmethod
    def _active_boutiques(request):
        from apps.tenants.models import Boutique

        return Boutique.objects.filter(
            is_active=True,
            memberships__user=request.user,
            memberships__is_active=True,
        ).select_related("compte")

    def _auto_select(self, request):
        boutiques = list(self._active_boutiques(request).distinct())
        if len(boutiques) == 1:
            return boutiques[0]
        for boutique in boutiques:
            if boutique.is_default:
                return boutique
        return None
