class CurrentTenantMiddleware:
    """Résout request.compte et request.boutique à partir de la session de
    l'utilisateur connecté (mode en ligne). En mode offline
    (settings.IS_OFFLINE), ces valeurs sont fixées une bonne fois pour
    toutes à l'activation du poste plutôt que résolues par session — voir
    _handle_offline() et apps.sync.models.DeviceActivation.

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
        request.is_compte_admin = False
        request.boutique_role = None

        from django.conf import settings

        if settings.IS_OFFLINE:
            return self._handle_offline(request)

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
                request.is_compte_admin = self._is_compte_admin(request, boutique.compte)
                request.boutique_role = self._role_for_boutique(request, boutique)

        return self.get_response(request)

    def _handle_offline(self, request):
        """Poste offline : la boutique est fixée une bonne fois pour
        toutes à l'activation (apps.sync.models.DeviceActivation), jamais
        résolue par session. Sans activation, toute requête (hors
        exemptions) est redirigée vers l'écran d'activation."""

        from django.shortcuts import redirect

        from apps.sync.models import DeviceActivation

        activation = DeviceActivation.get_active()
        if activation is None:
            if self._offline_activation_exempt(request):
                return self.get_response(request)
            return redirect("sync:activate")

        if request.user.is_authenticated:
            from apps.tenants.models import Boutique

            boutique = Boutique.objects.filter(id=activation.boutique_id).first()
            if boutique is not None:
                request.boutique = boutique
                request.compte = boutique.compte
                request.is_compte_admin = self._is_compte_admin(request, boutique.compte)
                request.boutique_role = self._role_for_boutique(request, boutique)

        return self.get_response(request)

    @staticmethod
    def _offline_activation_exempt(request):
        return request.path.startswith(("/api/v1/sync/activate/", "/static/", "/media/"))

    @staticmethod
    def _role_for_boutique(request, boutique):
        from apps.tenants.models import Membership

        membership = Membership.objects.filter(
            user=request.user, boutique=boutique, is_active=True
        ).first()
        return membership.role if membership else None

    @staticmethod
    def _is_compte_admin(request, compte):
        from apps.tenants.models import Membership

        return Membership.objects.filter(
            user=request.user,
            is_active=True,
            role=Membership.ADMIN_COMPTE,
            boutique__compte=compte,
        ).exists()

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
