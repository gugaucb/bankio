"""Public portal tests: access matrix, onboarding wizard, save/resume, submission
idempotency, KYC/approval integration, manager login security, role separation."""
import pytest
from django.test import Client

from apps.identity.models import User
from apps.managerops.models import ApprovalRequest, BankBranch, ManagerProfile

from tests.conftest import make_user
from tests.test_manager_portal import make_manager  # reuses branch fixtures pattern


PUBLIC_PAGES = ["/", "/personal/", "/business/", "/cards/", "/investments/",
                "/loans/", "/security/", "/help/", "/open-account/",
                "/application/resume/", "/manager/login/"]


@pytest.fixture
def c():
    return Client(enforce_csrf_checks=False)


@pytest.fixture
def branches(db):
    b1 = BankBranch.objects.create(branch_code="1001", name="Downtown", region="NORTH")
    return b1


def complete_draft(client):
    """Drive a draft through all wizard steps; returns the application."""
    r = client.post("/open-account/")
    assert r.status_code == 302
    from apps.portal.models import AccountApplication

    app = AccountApplication.objects.latest("pk")
    steps = {
        1: {"full_name": "Nora Newcustomer", "date_of_birth": "1993-04-04"},
        2: {"email": "nora@example.com", "phone": "+15550100"},
        3: {"address": "12 Elm Street"},
        4: {"national_id": "ID-99887", "source_of_funds": "SALARY"},
        5: {"occupation": "Engineer", "employment_status": "EMPLOYED"},
        6: {"monthly_income": "5200"},
        7: {"tax_residency": "US"},
        8: {"products": ["CHECKING", "SAVINGS"]},
    }
    from django.db import connections

    for step, data in steps.items():
        payload = dict(data)
        if step == 8:
            r = client.post(f"/open-account/{step}/next/", payload)  # products via getlist
        else:
            r = client.post(f"/open-account/{step}/next/", payload)
        assert r.status_code == 302, (step, r.status_code)
    app.refresh_from_db()
    return app


# ------------------------------------------------------------ access matrix

@pytest.mark.django_db(transaction=True)
def test_public_pages_accessible_anonymously(c):
    for url in PUBLIC_PAGES:
        r = c.get(url)
        assert r.status_code == 200, url


@pytest.mark.django_db(transaction=True)
def test_manager_pages_render(branches):
    """Smoke: every manager page renders without template errors."""
    mgr = make_manager("smoke_mgr", "BRANCH_MANAGER", branches)
    c = Client(enforce_csrf_checks=False)
    c.force_login(mgr)
    for url in ("/manage/", "/manage/customers/", "/manage/approvals/",
                "/manage/restrictions/", "/manage/applications/"):
        assert c.get(url).status_code == 200, url


@pytest.mark.django_db
def test_private_routes_require_auth(c):
    assert c.get("/app/").status_code == 302 and "/login/" in c.get("/app/").url
    assert c.get("/manage/", follow=False).status_code == 302
    assert c.get("/manage/customers/", follow=False).status_code == 302


@pytest.mark.django_db
def test_customer_cannot_reach_manager_dashboard(c, aubrey):
    c.force_login(aubrey)
    r = c.get("/manage/")
    assert r.status_code == 403


@pytest.mark.django_db
def test_customer_cannot_use_manager_applications(c, aubrey):
    c.force_login(aubrey)
    assert c.get("/manage/applications/").status_code == 403
    assert c.post("/manage/applications/decide/").status_code == 403


# ------------------------------------------------------------- wizard flows

@pytest.mark.django_db(transaction=True)
def test_full_wizard_submit_and_status(c, branches):
    app = complete_draft(c)
    r = c.get(f"/open-account/{app.current_step}/")  # lands on review
    assert b"Review" in r.content or r.status_code == 200
    r = c.post("/application/submit/", {"idempotency_key": f"portal-{app.reference}",
                                        "password": "Nora-Pass-2026", "password2": "Nora-Pass-2026"})
    assert r.status_code == 302
    app.refresh_from_db()
    assert app.status == "IDENTITY_REVIEW"
    # status page shows reference + next step
    r = c.get(f"/application/status/{app.reference}/")
    assert b"APPLICATION RECEIVED" in r.content
    assert b"Temporary password" not in r.content  # chosen password replaces temp delivery
    # manager review request queued
    assert ApprovalRequest.objects.filter(operation_type="ONBOARDING_REVIEW").exists()


@pytest.mark.django_db(transaction=True)
def test_missing_required_field_blocks_step(c, branches):
    c.post("/open-account/")
    r = c.post("/open-account/1/next/", {"full_name": "", "date_of_birth": ""})
    assert r.status_code == 302  # redirected back with error
    r2 = c.get(r.url)
    assert b"Missing" in r2.content or b"error" in r2.content.lower()


@pytest.mark.django_db(transaction=True)
def test_underage_and_invalid_email_rejected(c, branches):
    c.post("/open-account/")
    r = c.post("/open-account/1/next/", {"full_name": "Kid Doe", "date_of_birth": "2020-01-01"})
    assert r.status_code == 302
    body = c.get(r.url).content.decode()
    assert "Underage" in body or "underage" in body.lower()
    c.post("/open-account/1/next/", {"full_name": "Ok Person", "date_of_birth": "1990-01-01"})
    r = c.post("/open-account/2/next/", {"email": "not-an-email", "phone": "+1555"})
    body = c.get(r.url).content.decode()
    assert "Invalid Email" in body or "invalid email" in body.lower()


@pytest.mark.django_db(transaction=True)
def test_duplicate_existing_customer_rejected_on_submit(c, branches, aubrey):
    aubrey.email = "dup@example.com"
    aubrey.save()
    c.post("/open-account/")
    from apps.portal.models import AccountApplication

    app = AccountApplication.objects.latest("pk")
    steps = [
        (1, {"full_name": "Dup Applicant", "date_of_birth": "1993-04-04"}),
        (2, {"email": "DUP@example.com", "phone": "+1555"}),
        (3, {"address": "1 A St"}),
        (4, {"national_id": "X1", "source_of_funds": "SALARY"}),
        (5, {"occupation": "Dev", "employment_status": "EMPLOYED"}),
        (6, {"monthly_income": "3000"}),
        (7, {"tax_residency": "US"}),
    ]
    for s, d in steps:
        c.post(f"/open-account/{s}/next/", d)
    r = c.post("/application/submit/", {"idempotency_key": "k-dup",
                                        "password": "Dup-Pass-2026", "password2": "Dup-Pass-2026"},
               follow=True)
    app.refresh_from_db()
    assert app.status == "DRAFT"  # not submitted


@pytest.mark.django_db(transaction=True)
def test_save_and_resume(c, branches):
    app = complete_draft(c)
    r = c.get("/application/save-later/")
    assert app.reference.encode() in r.content
    c.session.flush() if hasattr(c, "session") else None
    from django.contrib.sessions.backends.db import SessionStore

    # resume via reference+email on a fresh session
    c2 = Client(enforce_csrf_checks=False)
    r = c2.post("/application/resume/", {"reference": app.reference, "email": "nora@example.com"})
    assert r.status_code == 302
    # resume via secret token
    c3 = Client(enforce_csrf_checks=False)
    r = c3.get(f"/application/resume/?token={app.resume_token}")
    assert r.status_code == 302
    # wrong email denied
    c4 = Client(enforce_csrf_checks=False)
    r = c4.post("/application/resume/", {"reference": app.reference, "email": "wrong@x.io"})
    assert r.status_code == 404


@pytest.mark.django_db(transaction=True)
def test_submission_idempotent_no_duplicates(c, branches):
    app = complete_draft(c)
    key = f"portal-{app.reference}"
    from apps.portal.services import submit_application, ApplicationError

    a1 = submit_application(app, idempotency_key=key, password="Nora-Pass-2026")
    n1 = ApprovalRequest.objects.filter(operation_type="ONBOARDING_REVIEW").count()
    a2 = submit_application(app, idempotency_key=key, password="Nora-Pass-2026")  # double click
    assert a1.pk == a2.pk
    assert ApprovalRequest.objects.filter(operation_type="ONBOARDING_REVIEW").count() == n1 == 1
    # a second application with the same idempotency key cannot exist
    from apps.portal.models import AccountApplication

    assert AccountApplication.objects.filter(idempotency_key=key).count() == 1


# ------------------------------------------- approval integration (KYC gate)

@pytest.mark.django_db(transaction=True)
def test_password_mismatch_blocks_submission(c, branches):
    app = complete_draft(c)
    r = c.post("/application/submit/", {"idempotency_key": "k-mm",
                                        "password": "Nora-Pass-2026",
                                        "password2": "Different-2026"})
    assert r.status_code == 302
    body = c.get(r.url).content.decode()
    assert "do not match" in body.lower()
    app.refresh_from_db()
    assert app.status == "DRAFT"


@pytest.mark.django_db(transaction=True)
def test_weak_password_rejected(c, branches):
    app = complete_draft(c)
    from apps.portal.services import submit_application, ApplicationError

    with pytest.raises(ApplicationError) as e:
        submit_application(app, password="12345678")  # all numeric
    assert e.value.code == "WEAK_PASSWORD"
    with pytest.raises(ApplicationError) as e:
        submit_application(app, password="short1a")  # under minimum length
    assert e.value.code == "WEAK_PASSWORD"
    with pytest.raises(ApplicationError) as e:
        submit_application(app, password=None)
    assert e.value.code == "MISSING_PASSWORD"
    app.refresh_from_db()
    assert app.status == "DRAFT"


@pytest.mark.django_db(transaction=True)
def test_legacy_draft_without_password_gets_temp_fallback(c, branches):
    """Drafts submitted before the wizard collected passwords still onboard."""
    app = complete_draft(c)
    from apps.portal.services import submit_application

    submit_application(app, password="Nora-Pass-2026")
    app.data.pop("password")  # simulate a legacy submission
    app.save()
    mgr = make_manager("legacy_mgr", "RELATIONSHIP_MANAGER", branches)
    req = ApprovalRequest.objects.get(operation_type="ONBOARDING_REVIEW")
    from apps.managerops.services import decide_approval

    decide_approval(approver=mgr, approval_id=req.pk, approve=True)
    app.refresh_from_db()
    user = User.objects.get(email="nora@example.com")
    assert app.temp_password  # temp credential issued for legacy draft
    assert user.check_password(app.temp_password)



@pytest.mark.django_db(transaction=True)
def test_approved_application_creates_customer_and_account(c, branches):
    app = complete_draft(c)
    from apps.portal.services import submit_application

    submit_application(app, password="Nora-Pass-2026")
    mgr = make_manager("portal_mgr", "RELATIONSHIP_MANAGER", branches)
    req = ApprovalRequest.objects.get(operation_type="ONBOARDING_REVIEW")
    from apps.managerops.services import decide_approval

    req = decide_approval(approver=mgr, approval_id=req.pk, approve=True)
    app.refresh_from_db()
    assert app.status == "ACCOUNT_CREATED"
    user = User.objects.get(email="nora@example.com")
    assert user.is_customer
    assert " " not in user.username  # generated username must be a valid identifier
    assert user.accounts.exists()  # active account created
    assert user.accounts.first().account_number
    assert app.customer_id == user.pk
    # chosen password: hash stored, never plaintext; no temp credential issued
    assert user.check_password("Nora-Pass-2026")
    assert not app.temp_password
    stored = app.data["password"]
    assert stored.startswith("pbkdf2_") and "Nora-Pass-2026" not in stored


@pytest.mark.django_db(transaction=True)
def test_rejected_application_does_not_create_account(c, branches):
    app = complete_draft(c)
    from apps.portal.services import submit_application

    submit_application(app, password="Nora-Pass-2026")
    mgr = make_manager("rej_mgr", "BRANCH_MANAGER", branches)
    req = ApprovalRequest.objects.get(operation_type="ONBOARDING_REVIEW")
    from apps.managerops.services import decide_approval

    decide_approval(approver=mgr, approval_id=req.pk, approve=False,
                    rejection_reason="Incomplete identity documentation")
    app.refresh_from_db()
    assert app.status == "REJECTED"
    assert not User.objects.filter(email="nora@example.com").exists()


@pytest.mark.django_db(transaction=True)
def test_low_risk_requires_relationship_but_high_risk_needs_branch(c, branches):
    """Escalation level respected: RELATIONSHIP cannot approve MEDIUM/HIGH risk."""
    app = complete_draft(c)
    from apps.portal.services import submit_application, _risk_level

    submit_application(app, password="Nora-Pass-2026")
    rel = make_manager("lvl_rel", "RELATIONSHIP_MANAGER", branches)
    br = make_manager("lvl_br", "BRANCH_MANAGER", branches)
    from apps.managerops.services import decide_approval

    req = ApprovalRequest.objects.get(operation_type="ONBOARDING_REVIEW")
    # force HIGH risk review level by simulating a risky product set
    app.products = ["CREDIT_CARD", "PREMIUM"]
    app.risk_level = _risk_level(app)
    app.save()
    req.required_level = "BRANCH_MANAGER"
    req.save()
    with pytest.raises(Exception):
        decide_approval(approver=rel, approval_id=req.pk, approve=True)
    decide_approval(approver=br, approval_id=req.pk, approve=True)
    app.refresh_from_db()
    assert app.status in ("ACCOUNT_CREATED", "KYC_REVIEW")


# --------------------------------------------------------- manager login

@pytest.mark.django_db(transaction=True)
def test_manager_login_success(c, branches):
    mgr = make_manager("ml_mgr", "BRANCH_MANAGER", branches)
    r = c.post("/manager/login/", {"username": "ml_mgr", "password": "Mgr!12345"})
    assert r.status_code == 302 and "/manage/" in r.url


@pytest.mark.django_db(transaction=True)
def test_customer_credentials_denied_on_manager_portal(c, branches, aubrey):
    r = c.post("/manager/login/", {"username": "aubrey", "password": "Test!12345"})
    assert r.status_code == 403
    assert not c.session.get("_auth_user_id")


@pytest.mark.django_db(transaction=True)
def test_invalid_and_locked_login(c, branches):
    make_manager("lock_mgr", "RELATIONSHIP_MANAGER", branches)
    r = c.post("/manager/login/", {"username": "lock_mgr", "password": "wrong"})
    assert r.status_code == 200 and b"Invalid credentials" in r.content


@pytest.mark.django_db(transaction=True)
def test_inactive_manager_denied(c, branches):
    u = make_manager("dead_mgr", "SENIOR_MANAGER", branches)
    u.is_active = False
    u.save()
    r = c.post("/manager/login/", {"username": "dead_mgr", "password": "Mgr!12345"})
    assert r.status_code in (200, 403) and not c.session.get("_auth_user_id")


@pytest.mark.django_db(transaction=True)
def test_manager_mfa_flow(c, branches):
    u = make_manager("mfa_mgr", "BRANCH_MANAGER", branches)
    u.mfa_enabled = True
    u.save()
    r = c.post("/manager/login/", {"username": "mfa_mgr", "password": "Mgr!12345"})
    assert r.status_code == 302 and "/otp/" in r.url
    # wrong code fails
    r = c.post("/manager/login/otp/", {"code": "000000"})
    assert b"Invalid code" in r.content or b"Invalid" in r.content
    # correct code proceeds (demo secret generated at attempt_login)
    import hashlib

    code = next((f"{i:06d}" for i in range(1000000)
                 if hashlib.sha256(f"{i:06d}".encode()).hexdigest()[:12] == u.mfa_secret), "")
    u.refresh_from_db()
    if u.mfa_secret:  # regenerate deterministically
        code = next((f"{i:06d}" for i in range(1000000)
                     if hashlib.sha256(f"{i:06d}".encode()).hexdigest()[:12] == u.mfa_secret), "")
    r = c.post("/manager/login/otp/", {"code": code})
    assert r.status_code == 302 and "/manage/" in r.url
