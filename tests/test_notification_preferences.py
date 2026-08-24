"""FASE 6 Branch 7 — notification preference tests."""
import pytest

from apps.notifications.models import Notification, NotificationPreference
from apps.notifications.services import (NotificationError, notify,
                                         set_category_preference)


def _user(username):
    from tests.conftest import make_user
    return make_user(username)


@pytest.mark.django_db
class TestPreferences:
    def test_default_enabled_without_row(self):
        u = _user("npf-a")
        n = notify(recipient=u, category="CARD", title="t", kind="X")
        assert n is not None
        assert NotificationPreference.objects.filter(user=u).count() == 0

    def test_off_mutes_future_events_only(self):
        u = _user("npf-b")
        before = notify(recipient=u, category="TRANSFER", title="old", kind="OLD_EVENT")
        set_category_preference(actor=u, category="TRANSFER", enabled=False)
        after = notify(recipient=u, category="TRANSFER", title="new", kind="NEW_EVENT")
        assert before is not None
        assert after is None  # future event dropped
        # existing rows untouched
        assert Notification.objects.filter(recipient=u).count() == 1

    def test_reenable_resumes(self):
        u = _user("npf-c")
        set_category_preference(actor=u, category="PAYMENT", enabled=False)
        assert notify(recipient=u, category="PAYMENT", title="x", kind="K1") is None
        set_category_preference(actor=u, category="PAYMENT", enabled=True)
        assert notify(recipient=u, category="PAYMENT", title="y", kind="K2") is not None

    def test_mandatory_kind_ignores_preference(self):
        from apps.notifications.services import MANDATORY_NOTIFICATION_KINDS
        u = _user("npf-d")
        for kind in ("USER_BLOCKED", "PASSWORD_CHANGED", "MFA_DISABLED",
                     "CHALLENGE_ISSUED"):
            assert kind in MANDATORY_NOTIFICATION_KINDS
            set_category_preference(actor=u, category="SECURITY", enabled=False)
            assert notify(recipient=u, category="SECURITY", title="sec",
                          kind=kind) is not None

    def test_non_mandatory_security_muted(self):
        u = _user("npf-e")
        set_category_preference(actor=u, category="SECURITY", enabled=False)
        assert notify(recipient=u, category="SECURITY", title="s",
                      kind="SOME_NON_MANDATORY_KIND") is None

    def test_preferences_change_audited_and_mass_assignment_safe(self):
        from apps.audit.models import AuditLog
        u = _user("npf-f")
        pref = set_category_preference(actor=u, category="CARD", enabled=False)
        assert pref.enabled is False
        assert AuditLog.objects.filter(
            action="NOTIFICATION_PREFERENCES_CHANGED").exists()
        with pytest.raises(NotificationError):
            set_category_preference(actor=u, category="HACKED", enabled=True)

    def test_unique_constraint_one_row_per_category(self):
        u = _user("npf-g")
        set_category_preference(actor=u, category="CARD", enabled=False)
        set_category_preference(actor=u, category="CARD", enabled=True)
        assert NotificationPreference.objects.filter(
            user=u, category="CARD").count() == 1


@pytest.mark.django_db
class TestPreferencesUI:
    def test_toggle_via_view_post(self, client):
        u = _user("npf-ui")
        client.force_login(u)
        resp = client.post("/app/notifications/", {
            "pref_category": "CARD", "pref_enabled": "0"})
        assert resp.status_code == 302
        assert not NotificationPreference.objects.get(
            user=u, category="CARD").enabled
        # invalid category ignored (no crash, no row)
        client.post("/app/notifications/", {"pref_category": "NOPE", "pref_enabled": "0"})
        assert NotificationPreference.objects.filter(user=u).count() == 1
        # cannot mutate another user's prefs (mass assignment / IDOR)
        other = _user("npf-ui2")
        client.force_login(other)
        client.post("/app/notifications/", {"pref_category": "CARD", "pref_enabled": "0"})
        assert NotificationPreference.objects.get(
            user=u, category="CARD").enabled is False
