"""FASE 6 Branch 2 — notification center UI tests."""
import pytest
from django.urls import reverse

from apps.notifications.services import notify


def _user(username):
    from tests.conftest import make_user
    from apps.customers.models import Customer
    u = make_user(username)
    Customer.objects.create(user=u, customer_number=f"C-{username}")
    return u


@pytest.mark.django_db
class TestNotificationCenter:
    def test_empty_state(self, client):
        u = _user("nu-a")
        client.force_login(u)
        r = client.get(reverse("app_notifications"))
        assert r.status_code == 200 and b"No notifications" in r.content

    def test_listing_and_badge_count(self, client):
        u = _user("nu-b")
        notify(recipient=u, category="TRANSFER", kind="TRANSFER_COMPLETED",
               title="Transfer completed", body="$10.00", dedup_key="T1")
        notify(recipient=u, category="SECURITY", title="Code sent")
        client.force_login(u)
        body = client.get(reverse("app_notifications")).content.decode()
        assert "Transfer completed" in body and "Code sent" in body

    def test_pagination_server_side(self, client):
        u = _user("nu-c")
        for i in range(25):
            notify(recipient=u, category="SYSTEM", title=f"n{i}", dedup_key=f"PG{i}")
        client.force_login(u)
        p1 = client.get(reverse("app_notifications")).content.decode()
        assert "Page 1 of 2" in p1
        p2 = client.get(reverse("app_notifications") + "?page=2").content.decode()
        assert "Page 2 of 2" in p2

    def test_ordering_deterministic(self, client):
        from apps.notifications.models import Notification
        u = _user("nu-d")
        a = notify(recipient=u, category="SYSTEM", title="first", dedup_key="O1")
        b = notify(recipient=u, category="SYSTEM", title="second", dedup_key="O2")
        assert Notification.objects.filter(recipient=u).values_list("pk", flat=True)[:2][0] == b.pk

    def test_mark_read_post_and_idempotent(self, client):
        u = _user("nu-e")
        n = notify(recipient=u, category="SYSTEM", title="r", dedup_key="R9")
        client.force_login(u)
        url = reverse("app_notification_read", args=[n.pk])
        assert client.get(url).status_code == 302          # GET is not destructive
        n.refresh_from_db(); assert not n.read
        r = client.post(url)
        n.refresh_from_db(); assert n.read and n.read_at is not None
        ts = n.read_at
        client.post(url)                                    # idempotent
        n.refresh_from_db(); assert n.read_at == ts

    def test_mark_all_read(self, client):
        u = _user("nu-f")
        for i in range(3):
            notify(recipient=u, category="SYSTEM", title=str(i), dedup_key=f"MA{i}")
        client.force_login(u)
        client.post(reverse("app_notifications_mark_all_read"))
        assert u.notifications.filter(read=False).count() == 0

    def test_filters(self, client):
        u = _user("nu-g")
        notify(recipient=u, category="TRANSFER", title="t1", dedup_key="F1")
        notify(recipient=u, category="CARD", title="c1", dedup_key="F2")
        client.force_login(u)
        base = reverse("app_notifications")
        assert "t1" in client.get(base + "?category=TRANSFER").content.decode()
        assert "c1" not in client.get(base + "?category=TRANSFER").content.decode()
        unread_only = client.get(base + "?state=unread&category=TRANSFER").content.decode()
        assert "t1" in unread_only
        # invalid category degrades safely to no filter
        assert "t1" in client.get(base + "?category=HACKED").content.decode()

    def test_idor_read_blocked(self, client):
        owner = _user("nu-h")
        intruder = _user("nu-i")
        n = notify(recipient=owner, category="SYSTEM", title="mine", dedup_key="ID1")
        client.force_login(intruder)
        r = client.post(reverse("app_notification_read", args=[n.pk]))
        assert r.status_code == 404
        n.refresh_from_db(); assert not n.read

    def test_xss_escaped(self, client):
        u = _user("nu-j")
        notify(recipient=u, category="SYSTEM",
               title="<script>alert(1)</script>", body="<b>bold</b>")
        client.force_login(u)
        body = client.get(reverse("app_notifications")).content.decode()
        assert "<script>alert(1)</script>" not in body

    def test_badge_decreases_after_read(self, client):
        u = _user("nu-k")
        n = notify(recipient=u, category="SYSTEM", title="badge", dedup_key="B1")
        client.force_login(u)
        shell = client.get(reverse("dashboard")).content.decode()
        assert ">1</span>" in shell
        client.post(reverse("app_notification_read", args=[n.pk]))
        shell2 = client.get(reverse("dashboard")).content.decode()
        assert ">1</span>" not in shell2

    def test_queries_controlled(self, client):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        u = _user("nu-l")
        for i in range(25):
            notify(recipient=u, category="SYSTEM", title=f"q{i}", dedup_key=f"Q{i}")
        client.force_login(u)
        with CaptureQueriesContext(connection) as ctx:
            client.get(reverse("app_notifications"))
        assert len(ctx.captured_queries) <= 12, len(ctx.captured_queries)

    def test_anonymous_redirects(self, client):
        r = client.get(reverse("app_notifications"))
        assert r.status_code == 302 and "/login/" in r.url
