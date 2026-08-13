from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as serve_static

from apps.core.views import service_worker

urlpatterns = [
    path("sw.js", service_worker, name="service_worker"),
    path("admin/", admin.site.urls),
    path("comptes/", include("apps.accounts.urls")),
    path("entreprises/", include("apps.tenants.urls")),
    path("catalogue/", include("apps.catalog.urls")),
    path("stock/", include("apps.stock.urls")),
    path("", include("apps.sales.urls")),
    path("", include("apps.core.urls")),
    path("api/v1/sync/", include("apps.sync.urls")),
]

if settings.DEBUG or settings.IS_OFFLINE:
    # DEBUG : mode dev classique. IS_OFFLINE : l'exe tourne avec DEBUG=False
    # (comme en prod) mais waitress n'écoute que sur 127.0.0.1 pour un seul
    # utilisateur local — aucun reverse proxy dédié aux médias comme en
    # ligne, donc Django doit servir /media/ lui-même ici, sans risque
    # puisque rien n'est exposé publiquement.
    #
    # django.conf.urls.static.static() a été essayé d'abord, mais elle
    # renvoie [] en interne dès que settings.DEBUG est False — elle ignore
    # silencieusement la condition ci-dessus. On enregistre donc la même
    # vue (django.views.static.serve) directement, sans passer par ce
    # helper qui n'est prévu que pour le mode DEBUG.
    urlpatterns += [
        re_path(r"^media/(?P<path>.*)$", serve_static, {"document_root": settings.MEDIA_ROOT}),
    ]
