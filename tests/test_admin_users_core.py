"""Branch 1 — admin user-management domain services: security + audit."""
import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import PermissionDenied

from apps.audit.models import AuditLog
from apps.identity.admin_services import (
    AdminUserError,
    block_user,
    create_user,
    get_user,
    list_users,
    unblock_user,
)
from apps.identity.models import Role, User
from tests.conftest import make_user


@pytest.fixture
def admin(db):
    return make_user("root-admin", role="ADMIN")


@pytest.fixture
def staff(db):
    return User.objects.create_user(
        "plain-staff", email="ps@t.io", password="Xx!12345678", role="SUPPORT_AGENT")


# ------------------------------------------------------------------ cadastro

@pytest.mark.django_db
def test_create_user_valid_hashes_password(admin):
    u = create_user(actor=admin, username="newbie", email="nb@t.io",
                    password="Sup3r-Secret!pass", role=Role.SUPPORT_AGENT)
    assert u.pk and u.check_password("Sup3r-Secret!pass")
    assert not u.password.startswith("Sup3r")  # hashed, never plaintext


@pytest.mark.django_db
def test_create_user_duplicate_rejected(admin):
    make_user("dup")
    with pytest.raises(AdminUserError) as e:
        create_user(actor=admin, username="other", email="dup@t.io",
                    password="Sup3r-Secret!pass", role=Role.CUSTOMER)
    assert e.value.code == "DUPLICATE"


@pytest.mark.django_db
def test_create_user_weak_password_rejected(admin):
    with pytest.raises(AdminUserError) as e:
        create_user(actor=admin, username="weakpw", email="wp@t.io",
                    password="123", role=Role.CUSTOMER)
    assert e.value.code == "WEAK_PASSWORD"


@pytest.mark.django_db
def test_create_user_invalid_role_rejected(admin):
    with pytest.raises(AdminUserError) as e:
        create_user(actor=admin, username="badrole", email="br@t.io",
                    password="Sup3r-Secret!pass", role="SUPERGOD")
    assert e.value.code == "INVALID_ROLE"


@pytest.mark.django_db
def test_create_requires_admin_role(staff):
    with pytest.raises(PermissionDenied):
        create_user(actor=staff, username="x", email="x@t.io",
                    password="Sup3r-Secret!pass", role=Role.CUSTOMER)


@pytest.mark.django_db
def test_no_mass_assignment_of_role_via_kwargs(admin):
    """create_user signature has no is_superuser/staff flags to abuse."""
    import inspect

    params = inspect.signature(create_user).parameters
    assert "is_superuser" not in params and "is_staff" not in params


@pytest.mark.django_db
def test_create_audited(admin):
    u = create_user(actor=admin, username="aud", email="aud@t.io",
                    password="Sup3r-Secret!pass", role=Role.CUSTOMER)
    ev = AuditLog.objects.get(action="ADMIN_USER_CREATED", resource_id=str(u.pk))
    assert ev.actor_id == admin.pk


# ------------------------------------------------------------------ bloqueio

@pytest.fixture
def target(db):
    return make_user("blockme", password="Target!12345")


@pytest.mark.django_db
def test_block_and_unblock_roundtrip(admin, target):
    block_user(actor=admin, user_id=target.pk, reason="suspeita de fraude")
    target.refresh_from_db()
    assert target.is_active is False
    # blocked user cannot authenticate (ModelBackend rejects inactive)
    assert authenticate(username="blockme", password="Target!12345") is None

    unblock_user(actor=admin, user_id=target.pk, reason="falso positivo")
    target.refresh_from_db()
    assert target.is_active is True
    assert authenticate(username="blockme", password="Target!12345") == target


@pytest.mark.django_db
def test_block_reason_required(admin, target):
    with pytest.raises(AdminUserError) as e:
        block_user(actor=admin, user_id=target.pk, reason="   ")
    assert e.value.code == "REASON_REQUIRED"
    with pytest.raises(AdminUserError) as e:
        unblock_user(actor=admin, user_id=target.pk, reason="")
    assert e.value.code == "REASON_REQUIRED"


@pytest.mark.django_db
def test_block_logs_reason_without_secrets(admin, target):
    block_user(actor=admin, user_id=target.pk, reason="motivo operacional X")
    ev = AuditLog.objects.get(action="ADMIN_USER_BLOCKED")
    assert ev.metadata["reason"] == "motivo operacional X"
    blob = str(ev.metadata).lower()
    for secret in ("password", "target!12345", "mfa_secret", "token"):
        assert secret not in blob


@pytest.mark.django_db
def test_self_block_forbidden(admin):
    with pytest.raises(AdminUserError) as e:
        block_user(actor=admin, user_id=admin.pk, reason="auto-block")
    assert e.value.code == "SELF_BLOCK"


@pytest.mark.django_db
def test_last_admin_cannot_be_blocked(db):
    root = User.objects.create_user(
        "su-root", email="sur@t.io", password="Xx!12345678",
        role=Role.CUSTOMER, is_superuser=True)
    sole = make_user("sole-admin", role="ADMIN")
    with pytest.raises(AdminUserError) as e:
        block_user(actor=root, user_id=sole.pk, reason="try last admin")
    assert e.value.code == "LAST_ADMIN"
    sole.refresh_from_db()
    assert sole.is_active is True


@pytest.mark.django_db
def test_block_nonexistent_user(admin):
    with pytest.raises(AdminUserError) as e:
        block_user(actor=admin, user_id=99999, reason="ghost")
    assert e.value.code == "USER_NOT_FOUND"


@pytest.mark.django_db
def test_block_twice_rejected(admin, target):
    block_user(actor=admin, user_id=target.pk, reason="a")
    with pytest.raises(AdminUserError) as e:
        block_user(actor=admin, user_id=target.pk, reason="b")
    assert e.value.code == "ALREADY_BLOCKED"


@pytest.mark.django_db
def test_unblock_active_user_rejected(admin, target):
    with pytest.raises(AdminUserError) as e:
        unblock_user(actor=admin, user_id=target.pk, reason="n/a")
    assert e.value.code == "ALREADY_ACTIVE"


@pytest.mark.django_db
def test_block_kills_active_sessions(admin, target, client):
    client.force_login(target)
    session_key = client.session.session_key
    block_user(actor=admin, user_id=target.pk, reason="sessões fora")
    from django.contrib.sessions.models import Session

    assert not Session.objects.filter(session_key=session_key).exists()


@pytest.mark.django_db
def test_block_does_not_delete_user_or_audit(admin, target):
    block_user(actor=admin, user_id=target.pk, reason="revogação")
    assert User.objects.filter(pk=target.pk).exists()
    assert AuditLog.objects.filter(action="ADMIN_USER_BLOCKED").exists()


@pytest.mark.django_db
def test_block_has_zero_financial_side_effects(admin, target):
    """INV: blocking a SYSTEM user never touches ledger/accounts/cards/fraud."""
    from apps.fraud.models import RiskEvaluation
    from apps.ledger.models import JournalEntry

    n_journal = JournalEntry.objects.count()
    n_eval = RiskEvaluation.objects.count()
    block_user(actor=admin, user_id=target.pk, reason="operação limpa")
    assert JournalEntry.objects.count() == n_journal
    assert RiskEvaluation.objects.count() == n_eval


# -------------------------------------------------------------- listagem/get

@pytest.mark.django_db
def test_list_users_query_status_pages(db):
    for i in range(23):
        make_user(f"page{i}")
    b = make_user("paged-blocked")
    b.is_active = False
    b.save()
    r = list_users(status="BLOCKED")
    assert r["total"] >= 1
    r = list_users(query="page0", status="ACTIVE")
    assert r["total"] == 1  # exact substring match on "page0"
    r = list_users(page=2, page_size=10)
    assert r["page"] == 2 and len(list(r["items"])) == 10


@pytest.mark.django_db
def test_get_user_none_for_missing(db):
    assert get_user(424242) is None


@pytest.mark.django_db
def test_services_require_authentication(db):
    with pytest.raises(PermissionDenied):
        block_user(actor=None, user_id=1, reason="x")
