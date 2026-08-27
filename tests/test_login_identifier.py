"""Login identifier: username OR email (portal applicants sign in with email)."""
import pytest
from django.test import RequestFactory

from apps.identity.models import User
from apps.identity.services import LoginLocked, attempt_login

PW = "Identifier-Pass-1"


@pytest.fixture
def user(db):
    return User.objects.create_user("nora.userabc123", email="Nora@Example.com",
                                    password=PW, role="CUSTOMER")


def _login(identifier, password=PW):
    rf = RequestFactory()
    req = rf.post("/login/", HTTP_USER_AGENT="ID/1")
    return attempt_login(identifier, password, req)


@pytest.mark.django_db
def test_login_by_username_still_works(user):
    auth_user, needs_otp = _login("nora.userabc123")
    assert auth_user is not None and auth_user.pk == user.pk


@pytest.mark.django_db
def test_login_by_email_works(user):
    auth_user, _ = _login("Nora@Example.com")
    assert auth_user is not None and auth_user.pk == user.pk


@pytest.mark.django_db
def test_login_by_email_case_insensitive(user):
    auth_user, _ = _login("nora@example.com")
    assert auth_user is not None and auth_user.pk == user.pk


@pytest.mark.django_db
def test_wrong_password_via_email_counts_on_user(user):
    auth_user, _ = _login("Nora@Example.com", "wrong-password")
    assert auth_user is None
    user.refresh_from_db()
    assert user.failed_login_count == 1


@pytest.mark.django_db
def test_lockout_enforced_via_email(user):
    from django.utils import timezone
    from datetime import timedelta
    user.locked_until = timezone.now() + timedelta(minutes=5)
    user.save(update_fields=["locked_until"])
    with pytest.raises(LoginLocked):
        _login("nora@example.com")


@pytest.mark.django_db
def test_unknown_identifier_returns_none(db):
    auth_user, _ = _login("ghost@example.com")
    assert auth_user is None
