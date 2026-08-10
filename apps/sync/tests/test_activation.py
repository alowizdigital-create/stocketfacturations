from unittest.mock import patch

import pytest
from django.test import Client as DjangoClient

from apps.sync.models import DeviceActivation

pytestmark = pytest.mark.django_db

URL = "/api/v1/sync/activate/"


def test_activation_404_when_online(settings):
    settings.IS_OFFLINE = False
    resp = DjangoClient().get(URL)
    assert resp.status_code == 404


def test_activation_success_creates_device_activation_and_pulls(settings):
    settings.IS_OFFLINE = True
    settings.SYNC_BASE_URL = "http://testserver.invalid"
    fake_ping = {
        "boutique": {"id": "11111111-1111-1111-1111-111111111111", "name": "Boutique Test"},
        "compte": {"id": "22222222-2222-2222-2222-222222222222", "name": "Entreprise Test"},
    }
    with patch("apps.sync.views.SyncClient") as MockClient, \
         patch("apps.sync.views.run_pull_cycle") as mock_pull:
        MockClient.return_value.ping.return_value = fake_ping

        resp = DjangoClient().post(URL, {"token": "un-jeton-quelconque"}, follow=False)

    assert resp.status_code == 302
    assert resp.url == "/comptes/connexion/"
    activation = DeviceActivation.get_active()
    assert activation is not None
    assert str(activation.boutique_id) == fake_ping["boutique"]["id"]
    mock_pull.assert_called_once()


def test_activation_form_error_on_connection_failure(settings):
    import requests

    settings.IS_OFFLINE = True
    settings.SYNC_BASE_URL = "http://testserver.invalid"
    with patch("apps.sync.views.SyncClient") as MockClient:
        MockClient.return_value.ping.side_effect = requests.ConnectionError("unreachable")
        resp = DjangoClient().post(URL, {"token": "un-jeton-quelconque"})

    assert resp.status_code == 200
    assert DeviceActivation.get_active() is None
    assert b"Connexion au serveur impossible" in resp.content


def test_reactivation_blocked_when_already_activated(settings):
    settings.IS_OFFLINE = True
    DeviceActivation.objects.create(
        boutique_id="11111111-1111-1111-1111-111111111111", boutique_name="Déjà activé",
        compte_id="22222222-2222-2222-2222-222222222222", compte_name="Compte",
        token="ancien-jeton",
    )

    resp = DjangoClient().get(URL)

    assert resp.status_code == 302
    assert DeviceActivation.objects.count() == 1
