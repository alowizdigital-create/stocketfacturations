import pytest
from django.test import Client as DjangoClient

from apps.accounts.models import User
from apps.tenants.models import Membership

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(boutique):
    user = User.objects.create_user(email="admin@test.local", password="testpass123")
    Membership.objects.create(user=user, boutique=boutique, role=Membership.ADMIN_COMPTE)
    client = DjangoClient()
    client.force_login(user)
    return client


def test_token_page_shows_no_token_initially(admin_client):
    resp = admin_client.get("/entreprises/parametres/jeton/")
    assert resp.status_code == 200
    assert b"Aucun jeton" in resp.content


def test_generate_token_shows_raw_value_once(admin_client):
    resp = admin_client.post("/entreprises/parametres/jeton/generer/")
    assert resp.status_code == 200
    assert b"raw-token-input" in resp.content

    # Un second GET (rechargement de la page) ne doit plus jamais montrer
    # le jeton en clair (le champ HTML lui-même), seulement son statut —
    # le JS de la page référence le même id sans exposer de valeur.
    follow_up = admin_client.get("/entreprises/parametres/jeton/")
    assert b'id="raw-token-input"' not in follow_up.content
    assert b"Actif" in follow_up.content
