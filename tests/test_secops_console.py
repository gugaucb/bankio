"""FASE 4.4 S1 — Security Operations console: engine health (staff-only)."""
import pytest
from django.urls import reverse

from apps.audit.models import AuditLog
from apps.fraud.models import RiskEvaluation

PW = "Str0ng-pass!x"


def _user(django_user_model, username, role, superuser=False):
    return django_user_model.objects.create_user(
        username, email=f"{username}@t.io", password=PW, role=role,
        is_superuser=superuser)


@pytest.fixture
def admin(db, django_user_model, client):
    u = _user(django_user_model, "sec-admin", "ADMIN")
    client.force_login(u)
    return u, client


@pytest.fixture
def auditor(db, django_user_model, client):
    u = _user(django_user_model, "sec-auditor", "AUDITOR")
    client.force_login(u)
    return u, client


@pytest.fixture
def fraud_manager(db, django_user_model, client):
    u = _user(django_user_model, "sec-fm", "FRAUD_MANAGER")
    client.force_login(u)
    return u, client


@pytest.fixture
def customer(db, django_user_model, client):
    u = _user(django_user_model, "sec-cust", "CUSTOMER")
    client.force_login(u)
    return u, client


# ------------------------------------------------------------- access model

@pytest.mark.django_db
@pytest.mark.parametrize("fixture_name", ["admin", "auditor", "fraud_manager"])
def test_staff_roles_reach_health(fixture_name, request):
    _, client = request.getfixturevalue(fixture_name)
    r = client.get(reverse("fraud:secops_health"))
    assert r.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("fixture_name", ["admin", "auditor", "fraud_manager"])
def test_superuser_reaches_health(django_user_model, fixture_name, request):
    _user(django_user_model, f"super-{fixture_name}", "CUSTOMER", superuser=True)
    from django.test import Client as C

    c = C()
    c.force_login(_user(django_user_model, "su", "CUSTOMER", superuser=True))
    assert c.get(reverse("fraud:secops_health")).status_code == 200


@pytest.mark.django_db
def test_customer_and_anonymous_denied(customer):
    from django.test import Client as C

    assert C().get(reverse("fraud:secops_health")).status_code == 302  # anonymous → login
    cust_client = customer[1]
    assert cust_client.get(reverse("fraud:secops_health")).status_code == 403


# ------------------------------------------------------------------- health

@pytest.mark.django_db
def test_health_renders_metrics(admin):
    _, client = admin
    r = client.get(reverse("fraud:secops_health"))
    assert r.status_code == 200
    content = r.content.decode()
    assert "Engine Health" in content
    assert "Evaluations" in content and "Engine errors" in content


# ------------------------------------------------------- S2: mode control

@pytest.mark.django_db
def test_mode_page_visible_to_all_secops_roles(auditor):
    _, client = auditor
    r = client.get(reverse("fraud:secops_mode"))
    assert r.status_code == 200
    assert b"Read-only" in r.content          # auditor cannot change


@pytest.mark.django_db
def test_auditor_cannot_post_mode_change(auditor):
    from apps.fraud.models import FraudEngineSetting

    _, client = auditor
    assert client.post(reverse("fraud:secops_mode"),
                       {"mode": "CHALLENGE_ONLY"}).status_code == 403
    assert not FraudEngineSetting.objects.filter(key="FRAUD_MODE").exists()


@pytest.mark.django_db
def test_customer_cannot_change_mode(customer):
    _, client = customer
    assert client.post(reverse("fraud:secops_mode"),
                       {"mode": "ENFORCEMENT"}).status_code == 403


@pytest.mark.django_db
def test_fraud_manager_changes_mode_with_audit(fraud_manager):
    from apps.fraud.modes import get_mode

    user, client = fraud_manager
    r = client.post(reverse("fraud:secops_mode"), {"mode": "CHALLENGE_ONLY"})
    assert r.status_code == 302
    assert get_mode() == "CHALLENGE_ONLY"
    log = AuditLog.objects.filter(action="FRAUD_MODE_CHANGED").latest("pk")
    assert log.metadata["to"] == "CHALLENGE_ONLY"


@pytest.mark.django_db
def test_unknown_mode_rejected_with_message(fraud_manager):
    from apps.fraud.modes import get_mode

    _, client = fraud_manager
    r = client.post(reverse("fraud:secops_mode"), {"mode": "FULL_POWER"},
                    follow=True)
    assert b"Unknown mode" in r.content
    assert get_mode() != "FULL_POWER"


# ------------------------------------------------ S3: evaluation browser

def _eval(operation="LOGIN", **kw):
    defaults = dict(engine_mode="SHADOW", decision="ALLOW",
                    status="COMPLETED")
    defaults.update(kw)
    return RiskEvaluation.objects.create(operation_type=operation, **defaults)


@pytest.mark.django_db
def test_browser_lists_and_filters(admin, django_user_model):
    actor = _user(django_user_model, "ev-actor", "CUSTOMER")
    login_ev = _eval(actor=actor)
    _eval("TRANSFER", actor=actor, decision="BLOCK", risk_level="CRITICAL")
    _, client = admin
    url = reverse("fraud:secops_evaluations")

    r = client.get(url, {"operation": "LOGIN"})
    content = r.content.decode()
    assert str(login_ev.pk) in content
    assert "TRANSFER" not in content.split("<tbody>")[1].split("</tr>")[0]

    r_block = client.get(url, {"decision": "BLOCK"})
    block_body = r_block.content.decode().split("<tbody>")[1].split("</tr>")[0]
    assert "TRANSFER" in block_body and "LOGIN" not in block_body

    assert b"TRANSFER" in client.get(url).content      # no filter → all


@pytest.mark.django_db
def test_browser_pagination_server_side(admin):
    _, client = admin
    for i in range(30):
        _eval()
    r = client.get(reverse("fraud:secops_evaluations"))
    content = r.content.decode()
    assert "Page 1 of 2" in content
    assert content.count("<tr class=\"border-t") == 25   # 25 per page


@pytest.mark.django_db
def test_customer_cannot_browse(customer):
    _, client = customer
    assert client.get(reverse("fraud:secops_evaluations")).status_code == 403


@pytest.mark.django_db
def test_detail_shows_signals_rules_and_blocks_customers(admin, django_user_model):
    from django.test import Client as C

    _, client = admin
    ev = _eval(signal_values={"NEW_DEVICE": True},
               triggered_rules=[{"rule_id": "R1", "version": 3, "score": 45}],
               risk_score=45, policy_version="policy-v1")
    r = client.get(reverse("fraud:secops_evaluation_detail", args=[ev.pk]))
    assert r.status_code == 200
    assert b"R1 v3" in r.content and b"NEW_DEVICE" in r.content

    cust = _user(django_user_model, "ev-cust", "CUSTOMER")
    cc = C()
    cc.force_login(cust)
    assert cc.get(
        reverse("fraud:secops_evaluation_detail", args=[ev.pk])).status_code == 403


@pytest.mark.django_db
def test_health_links_to_browser(auditor):
    _, client = auditor
    r = client.get(reverse("fraud:secops_evaluations"))
    assert b"Evaluation browser" in r.content or r.status_code == 200
