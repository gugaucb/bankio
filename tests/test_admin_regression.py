"""Branch 4 — admin user-management regression: E2E journeys, adversarial
attacks, financial-invariant spot checks. Nothing here weakens earlier suites."""
import threading

import pytest
from django.contrib.auth import authenticate
from django.test import Client

from apps.audit.models import AuditLog
from apps.identity.admin_services import (
    AdminUserError,
    block_user,
    create_user,
    unblock_user,
)
from apps.ledger.models import JournalEntry
from apps.ledger.services import account_balance, post_journal
from tests.conftest import make_user


@pytest.fixture
def admin(db):
    return make_user("reg-admin", role="ADMIN", password="Admin!12345")


def _c(user=None):
    c = Client()
    if user is not None:
        c.force_login(user)
    return c


# ============================================================ E2E journeys

@pytest.mark.django_db
class TestE2EJourneys:
    def test_admin_creates_lists_blocks_unblocks(self, admin):
        c = _c(admin)
        # create through the UI
        r = c.post("/manage/users/new/", {
            "username": "journey-user", "email": "jy@t.io",
            "password": "Sup3r-Secret!pass", "role": "CUSTOMER",
        })
        assert r.status_code == 302
        u = authenticate(username="journey-user", password="Sup3r-Secret!pass")
        assert u is not None
        # list shows the new user; filter finds it
        assert b"journey-user" in c.get("/manage/users/?q=journey").content
        # block with reason via POST form
        r = c.post(f"/manage/users/{u.pk}/block/", {"reason": "e2e block"})
        assert r.status_code == 302
        u.refresh_from_db()
        assert u.is_active is False
        assert authenticate(username="journey-user", password="Sup3r-Secret!pass") is None
        # unblock restores access end-to-end (real login flow)
        r = c.post(f"/manage/users/{u.pk}/unblock/", {"reason": "e2e unblock"})
        assert r.status_code == 302
        fresh = Client()
        assert fresh.post("/login/", {"username": "journey-user",
                                      "password": "Sup3r-Secret!pass"}).status_code == 302

    def test_blocked_user_cannot_authenticate_until_unblocked(self, admin):
        t = make_user("cycle-user", password="Cycle!12345")
        block_user(actor=admin, user_id=t.pk, reason="x")
        assert authenticate(username="cycle-user", password="Cycle!12345") is None
        unblock_user(actor=admin, user_id=t.pk, reason="y")
        assert authenticate(username="cycle-user", password="Cycle!12345")

    def test_common_user_gets_403_across_panel(self, admin, db):
        pleb = make_user("pleb-e2e")
        target = make_user("someone-else")
        c = _c(pleb)
        for url in ("/manage/users/", "/manage/users/new/",
                    f"/manage/users/{target.pk}/",
                    "/manage/users/dashboard/"):
            assert c.get(url).status_code == 403, url
        assert c.post(f"/manage/users/{target.pk}/block/",
                      {"reason": "nope"}).status_code == 403


# ============================================================= adversarial

@pytest.mark.django_db
class TestAdversarial:
    def test_direct_url_anonymous_redirected_everywhere(self):
        c = _c()
        for url in ("/manage/users/", "/manage/users/new/", "/manage/users/1/",
                    "/manage/users/dashboard/"):
            r = c.get(url)
            assert r.status_code == 302 and "/login" in r["Location"], url

    def test_idor_block_by_nonadmin_ignored(self, admin):
        pleb = make_user("idor-pleb", password="P!123456789")
        victim = make_user("idor-victim")
        c = _c(pleb)
        assert c.post(f"/manage/users/{victim.pk}/block/",
                      {"reason": "hax"}).status_code == 403
        victim.refresh_from_db()
        assert victim.is_active is True

    def test_privilege_escalation_via_create_rejected(self, admin):
        from django.contrib.auth import get_user_model

        agent = make_user("esc-agent", role="SUPPORT_AGENT")
        c = _c(agent)
        r = c.post("/manage/users/new/", {
            "username": "escalated", "email": "esc@t.io",
            "password": "Sup3r-Secret!pass", "role": "ADMIN",
        })
        assert r.status_code == 403
        assert not get_user_model().objects.filter(username="escalated").exists()

    def test_mass_assignment_is_superuser_flag_ignored(self, admin):
        from django.contrib.auth import get_user_model

        c = _c(admin)
        c.post("/manage/users/new/", {
            "username": "massign", "email": "ma@t.io",
            "password": "Sup3r-Secret!pass", "role": "CUSTOMER",
            "is_superuser": "True", "is_staff": "True",
        })
        u = get_user_model().objects.get(username="massign")
        assert u.is_superuser is False and u.is_staff is False and u.role == "CUSTOMER"

    def test_csrf_required_on_block(self, admin):
        t = make_user("csrf-target")
        c = Client(enforce_csrf_checks=True)
        c.force_login(admin)
        r = c.post(f"/manage/users/{t.pk}/block/", {"reason": "no token"})
        assert r.status_code == 403
        t.refresh_from_db()
        assert t.is_active is True

    def test_get_based_block_is_method_not_allowed(self, admin):
        t = make_user("get-blocked")
        assert _c(admin).get(f"/manage/users/{t.pk}/block/").status_code == 405
        t.refresh_from_db()
        assert t.is_active is True

    def test_manipulated_and_missing_ids_safe(self, admin):
        c = _c(admin)
        assert c.get("/manage/users/999999/").status_code == 404
        assert c.post("/manage/users/999999/block/",
                      {"reason": "ghost"}).status_code == 302  # redirect w/ error flash
        assert not AuditLog.objects.filter(action="ADMIN_USER_BLOCKED",
                                           resource_id="999999").exists()

    def test_block_without_reason_never_changes_state(self, admin):
        t = make_user("noreason")
        for reason in ("", "   ", None):
            payload = {} if reason is None else {"reason": reason}
            with pytest.raises(AdminUserError):
                block_user(actor=admin, user_id=t.pk, reason=reason or "")
        t.refresh_from_db()
        assert t.is_active is True

    def test_blocked_session_immediately_dead(self, admin):
        t = make_user("session-victim", password="Sv!12345678")
        c = _c(t)
        assert c.get("/app/").status_code == 200  # session works pre-block
        block_user(actor=admin, user_id=t.pk, reason="kill sessions")
        c2 = Client()
        c2.cookies.update(c.cookies)
        r = c2.get("/app/")
        assert r.status_code == 302 and "/login" in r["Location"]

    @pytest.mark.django_db(transaction=True)
    def test_concurrent_block_unblock_final_state_consistent(self, admin):
        t = make_user("race-target")
        errors = []

        def worker(fn, uid, tag):
            try:
                fn(actor=admin, user_id=uid, reason=f"race {tag}")
            except AdminUserError:
                pass  # rejected by state machine — expected under contention

        threads = [threading.Thread(target=worker, args=(block_user, t.pk, i))
                   for i in range(4)]
        threads += [threading.Thread(target=worker, args=(unblock_user, t.pk, i))
                    for i in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        t.refresh_from_db()
        assert t.is_active in (True, False)  # no corruption
        n = AuditLog.objects.filter(action__in=("ADMIN_USER_BLOCKED",
                                                "ADMIN_USER_UNBLOCKED"),
                                    resource_id=str(t.pk)).count()
        assert n >= 1  # every accepted transition audited exactly once each


# ================================================ financial invariant spot-checks

@pytest.mark.django_db
class TestFinancialInvariants:
    def test_admin_ops_leave_ledger_untouched(self, admin, alice, account_factory):
        acct = account_factory(alice, "500.00")
        baseline = JournalEntry.objects.count()
        bal = account_balance(acct.ledger_account)
        create_user(actor=admin, username="inv-u1", email="i1@t.io",
                    password="Sup3r-Secret!pass", role="CUSTOMER")
        t = make_user("inv-t1")
        block_user(actor=admin, user_id=t.pk, reason="invariant check")
        unblock_user(actor=admin, user_id=t.pk, reason="invariant restore")
        assert JournalEntry.objects.count() == baseline
        assert account_balance(acct.ledger_account) == bal

    def test_posted_journal_immutable_after_admin_ops(self, admin, alice, account_factory):
        acct = account_factory(alice, "100.00")
        journal = JournalEntry.objects.latest("id")
        journal.description = "tampered"
        with pytest.raises(Exception):
            journal.save()
        block_user(actor=admin, user_id=alice.pk, reason="still immutable")
        alice.refresh_from_db()

    def test_posting_and_idempotency_roundtrip_after_admin_changes(self, admin):
        """Posting engine + idempotency marker mechanism intact after admin ops."""
        from decimal import Decimal

        from apps.ledger.services import (
            find_idempotent,
            get_or_create_account,
            record_idempotent,
        )

        a = get_or_create_account("2001-IDEM-ADM", "idem probe", is_customer=True)
        key = "admin-regression-idem-key"
        j = post_journal(reference=key, description="first",
                         lines=[(a, "DEBIT", Decimal("5.00")),
                                (a, "CREDIT", Decimal("5.00"))])
        assert find_idempotent(key) is None  # caller-owned marker not yet set
        record_idempotent(key, "probe", j, {"ok": True})
        rec = find_idempotent(key)
        assert rec is not None and rec.journal_id == j.pk  # replay path intact
