import pytest

from .factories import MembershipFactory, UserFactory

pytestmark = pytest.mark.django_db

URL = "/api/v1/sync/pull/accounts/users/"


def test_pull_users_scoped_to_boutique_not_whole_compte(api_client, boutique):
    same_boutique_membership = MembershipFactory(boutique=boutique)
    other_boutique_membership = MembershipFactory()  # autre boutique, autre compte

    resp = api_client.get(URL)

    assert resp.status_code == 200
    ids = {item["id"] for item in resp.data["results"]}
    assert str(same_boutique_membership.user_id) in ids
    assert str(other_boutique_membership.user_id) not in ids


def test_pull_users_never_exposes_plaintext_password(api_client, boutique):
    membership = MembershipFactory(boutique=boutique)
    raw_password = "testpass123"

    resp = api_client.get(URL)

    item = next(r for r in resp.data["results"] if r["id"] == str(membership.user_id))
    assert item["password"] != raw_password
    assert item["password"].count("$") >= 2  # forme d'un hachage Django (algo$...$...)


def test_pull_users_inactive_membership_excluded(api_client, boutique):
    membership = MembershipFactory(boutique=boutique, is_active=False)

    resp = api_client.get(URL)

    ids = {item["id"] for item in resp.data["results"]}
    assert str(membership.user_id) not in ids
