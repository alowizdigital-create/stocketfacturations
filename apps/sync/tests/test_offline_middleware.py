import pytest
from django.test import Client as DjangoClient

from apps.sync.models import DeviceActivation

from .factories import BoutiqueFactory, MembershipFactory

pytestmark = pytest.mark.django_db


def test_no_activation_redirects_to_activate(settings):
    settings.IS_OFFLINE = True
    resp = DjangoClient().get("/")
    assert resp.status_code == 302
    assert resp.url == "/api/v1/sync/activate/"


def test_activation_url_itself_is_not_redirected(settings):
    settings.IS_OFFLINE = True
    resp = DjangoClient().get("/api/v1/sync/activate/")
    assert resp.status_code == 200


def test_activated_boutique_is_pinned_regardless_of_session(settings):
    settings.IS_OFFLINE = True
    boutique = BoutiqueFactory()
    membership = MembershipFactory(boutique=boutique)
    DeviceActivation.objects.create(
        boutique_id=boutique.id, boutique_name=boutique.name,
        compte_id=boutique.compte_id, compte_name=boutique.compte.name,
        token="irrelevant-for-this-test",
    )

    client = DjangoClient()
    client.force_login(membership.user)
    resp = client.get("/")

    assert resp.status_code == 200
    # La boutique connectée doit apparaître (fil d'ariane/sidebar de
    # base.html, affichée uniquement si request.boutique a été résolu).
    assert boutique.name.encode() in resp.content


def test_online_mode_untouched_by_offline_branch():
    # settings.IS_OFFLINE reste False (valeur par défaut de pytest.ini,
    # config.settings.dev) — la redirection d'activation ne doit jamais
    # se déclencher en ligne, même sans activation locale.
    resp = DjangoClient().get("/")
    assert resp.status_code != 302 or resp.url != "/api/v1/sync/activate/"
