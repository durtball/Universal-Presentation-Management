"""Site offline credential and RBAC boundary tests."""

from uuid import uuid4

from upm_shared.contracts.deployments import SiteUserSnapshot
from upm_site.auth import has_permission, hash_password, verify_password
from upm_site.persistence.models import User


def user(role="read_only", permissions=None):
    return User(
        user_id=uuid4(),
        username="operator",
        normalized_username="operator",
        display_name="Operator",
        user_type="site_local",
        roles=[role],
        permissions=permissions or [],
    )


def test_site_verifier_authenticates_offline_without_plaintext_storage():
    verifier = hash_password("a secure offline password")
    assert "a secure offline password" not in verifier
    assert verify_password("a secure offline password", verifier)
    assert not verify_password("wrong password", verifier)


def test_site_rbac_is_server_authoritative():
    assert has_permission(user(), "presentations.read")
    assert not has_permission(user(), "presentations.write")
    assert has_permission(user("operator"), "presentations.write")
    assert has_permission(user("administrator"), "users.manage")


def test_projection_carries_verifier_not_plaintext_or_smb_secret():
    fields = SiteUserSnapshot.model_fields
    assert "password_verifier" in fields
    assert "password" not in fields
    assert "smb_password" not in fields
