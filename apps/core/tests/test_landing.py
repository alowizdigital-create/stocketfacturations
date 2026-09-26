"""Page d'accueil publique : visible pour un visiteur, jamais à la place du tableau de bord."""

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.core.views import DOWNLOAD_FILENAME
from apps.sync.tests.factories import BoutiqueFactory
from apps.tenants.models import Membership

pytestmark = pytest.mark.django_db


def test_visitor_sees_landing_page(client):
    response = client.get("/")
    assert response.status_code == 200
    html = response.content.decode()
    assert "Zweey" in html and "Créer mon entreprise" in html
    assert reverse("tenants:signup") in html and reverse("accounts:login") in html
    assert "landing.css" in html
    # aucune donnée d'une entreprise ne fuite dans une page publique
    assert "Tableau de bord" not in html.split("<main")[0]


def test_landing_is_indexable_and_titled(client):
    html = client.get("/").content.decode()
    assert "<title>Zweey" in html and 'name="description"' in html


def test_logged_in_user_still_gets_dashboard(client):
    boutique = BoutiqueFactory()
    user = User.objects.create_user(email="u@test.com", password="x")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.ADMIN_COMPTE, is_active=True)
    client.force_login(user)
    html = client.get("/").content.decode()
    assert "Chiffre d'affaires du mois" in html and "Créer mon entreprise" not in html


def test_offline_workstation_has_no_landing(client, settings):
    settings.IS_OFFLINE = True
    response = client.get("/")
    assert response.status_code == 302
    # sans activation : vers l'écran d'activation ; activé : vers la connexion
    assert "/api/v1/sync/activate/" in response.url or "connexion" in response.url


def test_landing_links_resolve(client):
    for url in ("/comptes/connexion/", "/entreprises/inscription/"):
        assert client.get(url).status_code == 200


def test_download_button_hidden_without_the_file(client):
    """Pas de lien mort tant que l'exe n'a pas été déposé sur le serveur
    (voir DEPLOY.md) — le dossier n'existe même pas dans un environnement de
    test fraîchement installé."""
    html = client.get("/").content.decode()
    assert "Télécharger pour Windows" not in html


def test_download_button_shown_once_file_is_deployed(client, tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / DOWNLOAD_FILENAME).write_bytes(b"fake zip")

    html = client.get("/").content.decode()
    assert "Télécharger pour Windows" in html
    assert f"/media/downloads/{DOWNLOAD_FILENAME}" in html
