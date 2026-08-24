"""FASE 6 Branch 6 — security-event notification integration tests."""
import pytest

from apps.notifications.models import Notification
from apps.notifications.services import MANDATORY_NOTIFICATION_KINDS


def _user(username, **kw):
    from tests.conftest import make_user
    return make_user(username, **kw)


def _request(rf):
    r = rf.get("/app/security/")
    r.META["HTTP_USER_AGENT"] = "SecTestAgent/1.0"
    r.META.setdefault("HTTP_ACCEPT_LANGUAGE", "en")
    if not hasattr(r, "session"):
        from django.contrib.sessions.backends.db import SessionStore
        r.session = SessionStore()
    return r


@pytest.mark.django_db
class TestSecurityEvents:
    def test_mandatory_kinds_covered(self):
        covered = {"PASSWORD_CHANGED", "MFA_DISABLED", "MFA_ENABLED",
                   "NEW_DEVICE", "USER_BLOCKED", "USER_UNBLOCKED"}
        assert covered <= MANDATORY_NOTIFICATION_KINDS

    def test_new_device_fires_once_per_device(self, rf):
        from apps.identity.services import register_device
        u = _user("se-a")
        req = _request(rf)
        d1 = register_device(u, req)
        n = Notification.objects.get(recipient=u, kind="NEW_DEVICE")
        assert n.category == "SECURITY"
        # same device again → get_or_create hits existing row → no new note
        register_device(u, req)
        assert Notification.objects.filter(recipient=u, kind="NEW_DEVICE").count() == 1
        assert d1.device_id

    def test_trusted_device_relogin_not_new(self, rf):
        from apps.identity.services import is_new_device, register_device
        u = _user("se-b")
        req = _request(rf)
        device = register_device(u, req)
        Notification.objects.all().delete()
        device.trusted = True
        device.save(update_fields=["trusted"])
        assert not is_new_device(u, req)
        register_device(u, req)  # same hash → no new row → still no NEW_DEVICE
        assert not Notification.objects.filter(recipient=u, kind="NEW_DEVICE").exists()

    def test_mfa_enable_and_disable_notify(self, rf):
        from apps.identity.services import MFAError, confirm_mfa_enable, disable_mfa, start_mfa_enable
        u = _user("se-c")
        req = _request(rf)
        code = start_mfa_enable(u, request=req)
        confirm_mfa_enable(u, code, request=req)
        assert Notification.objects.filter(recipient=u, kind="MFA_ENABLED").exists()
        disable_mfa(u, "Test!12345", request=req)
        note = Notification.objects.get(recipient=u, kind="MFA_DISABLED")
        assert "disabled" in note.body.lower()
        with pytest.raises(MFAError):
            disable_mfa(u, "wrong", request=req)

    def test_password_changed_notifies_via_view(self, client, django_user_model):
        u = django_user_model.objects.create_user("se-d", password="old-pw-123")
        client.force_login(u)
        resp = client.post("/app/security/", {
            "change_password": "1",
            "old_password": "old-pw-123",
            "new_password1": "new-pw-456!",
            "new_password2": "new-pw-456!",
        })
        assert resp.status_code in (200, 302)
        assert Notification.objects.filter(recipient=u, kind="PASSWORD_CHANGED").exists()


@pytest.mark.django_db(transaction=True)
class TestAdminBlockNotifications:
    def test_block_and_unblock_notify_target_once(self):
        from apps.identity.admin_services import block_user, unblock_user
        admin = _user("se-admin", role="ADMIN")
        target = _user("se-target")
        blocked = block_user(actor=admin, user_id=target.pk, reason="fraud review")
        assert not blocked.is_active
        note = Notification.objects.get(recipient=target, kind="USER_BLOCKED")
        # reason text must NOT leak to the target notification
        assert "fraud review" not in note.body + str(note.metadata)
        unblock_user(actor=admin, user_id=target.pk, reason="cleared")
        Notification.objects.get(recipient=target, kind="USER_UNBLOCKED")
        kinds = list(Notification.objects.filter(recipient=target).values_list("kind", flat=True))
        assert kinds.count("USER_BLOCKED") == 1 and kinds.count("USER_UNBLOCKED") == 1
