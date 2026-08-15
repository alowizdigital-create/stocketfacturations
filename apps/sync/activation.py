import requests
from django.conf import settings

from .client import SyncClient
from .models import DeviceActivation


def get_active_client():
    """Construit un SyncClient à partir de l'activation locale. Lève si
    ce poste n'a pas encore été activé — voir apps.sync.views.activate_device."""

    activation = DeviceActivation.get_active()
    if activation is None:
        raise RuntimeError("Poste non activé.")
    return SyncClient(base_url=settings.SYNC_BASE_URL, token=activation.token)


def is_auth_error(exc):
    """Distingue un rejet explicite du jeton (401/403 — jeton régénéré ou
    désactivé côté serveur) d'une simple panne réseau/serveur, qui reste un
    incident transitoire à retenter au prochain cycle sans rien signaler à
    l'utilisateur."""

    return (
        isinstance(exc, requests.exceptions.HTTPError)
        and exc.response is not None
        and exc.response.status_code in (401, 403)
    )


def mark_token_invalid():
    """Le jeton local ne correspond plus à celui attendu par le serveur —
    plus aucune synchro ne peut réussir tant qu'il n'est pas remplacé.
    Renvoie l'utilisateur vers l'écran d'activation (voir
    CurrentTenantMiddleware._handle_offline) au lieu de tenter
    indéfiniment des cycles voués à l'échec."""

    DeviceActivation.objects.update(token_invalid=True)
