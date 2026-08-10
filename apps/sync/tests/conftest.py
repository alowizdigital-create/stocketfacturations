import pytest
from rest_framework.test import APIClient

from .factories import BoutiqueFactory, ProductFactory, issue_token


@pytest.fixture
def boutique(db):
    return BoutiqueFactory()


@pytest.fixture
def token_pair(boutique):
    return issue_token(boutique)


@pytest.fixture
def raw_token(token_pair):
    return token_pair[1]


@pytest.fixture
def product(boutique):
    return ProductFactory(compte=boutique.compte)


@pytest.fixture
def api_client(raw_token):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw_token}")
    return client


@pytest.fixture
def anon_client():
    return APIClient()
