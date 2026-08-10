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
