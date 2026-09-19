from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as serve_static

from apps.core.views import service_worker

urlpatterns = [
    path("sw.js", service_worker, name="service_worker"),
    path("admin/", admin.site.urls),
    # Vue standard Django pour le sélecteur de langue (templates/base.html) —
    # POST-only, bascule request.session[LANGUAGE_SESSION_KEY] puis
    # redirige vers `next`. Pas de préfixe /fr//en/ dans les URLs (pas
    # i18n_patterns) : casserait les chemins fixes attendus par le poste
    # offline (ex: /api/v1/sync/activate/, testé en dur dans apps/sync/tests).
    path("i18n/", include("django.conf.urls.i18n")),
    path("comptes/", include("apps.accounts.urls")),
    path("entreprises/", include("apps.tenants.urls")),
    path("catalogue/", include("apps.catalog.urls")),
    path("stock/", include("apps.stock.urls")),
    path("caisse/", include("apps.cashier.urls")),
    path("", include("apps.sales.urls")),
    path("", include("apps.core.urls")),
    path("api/v1/sync/", include("apps.sync.urls")),
]

# Toujours servi par Django, DEBUG ou pas : il n'y a pas de bloc dédié aux
# médias au niveau du reverse proxy (Dokploy/Caddy) pour ce déploiement à
# un seul conteneur — sans cette route, /media/ renvoie 404 dès que
# DEBUG=False (observé en réel : photos produits/logo cassées en
# production). Même topo pour l'exe offline (waitress n'écoute que sur
# 127.0.0.1, aucun risque à exposer ça soi-même).
#
# django.conf.urls.static.static() a été essayé d'abord, mais elle renvoie
# [] en interne dès que settings.DEBUG est False — elle ignore
# silencieusement toute condition qu'on mettrait autour. On enregistre donc
# la même vue (django.views.static.serve) directement, sans passer par ce
# helper qui n'est prévu que pour le mode DEBUG.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve_static, {"document_root": settings.MEDIA_ROOT}),
]
