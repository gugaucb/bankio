"""Immutability and reversals, including DB-level bypass attempts."""
from decimal import Decimal

import pytest
from django.contrib.admin import AdminSite

from apps.ledger import services as ledger
from apps.ledger.admin import JournalEntryAdmin
from apps.ledger.models import JournalEntry


@pytest.fixture
def accounts(db):
    cash = ledger.get_or_create_account("IM-CASH", "Cash", type="ASSET")
    rev = ledger.get_or_create_account("IM-REV", "Revenue", type="INCOME")
    return cash, rev


def _post(cash, rev, ref, amount="10.00"):
    return ledger.post_journal(ref, "x", [(cash, "DEBIT", amount), (rev, "CREDIT", amount)])


def test_queryet_update_on_posted_journal_rejected(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "IM-UPD")
    with pytest.raises(Exception, match="immutable"):
        JournalEntry.objects.filter(pk=j.pk).update(description="tampered")


def test_queryset_delete_of_posted_journal_rejected(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "IM-DEL")
    with pytest.raises(Exception):
        JournalEntry.objects.filter(pk=j.pk).delete()


def test_raw_reference_change_rejected(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "IM-REF")
    j.reference = "EVIL"
    with pytest.raises(Exception):
        j.save()


def test_reverses_link_still_permitted_after_posting(accounts):
    """The ONLY allowed mutation of a posted journal is linking its reversal."""
    cash, rev = accounts
    j = _post(cash, rev, "IM-LINK")
    r = ledger.reverse_journal(j)
    assert r.reverses == j
    r.refresh_from_db()
    assert r.reverses_id == j.id


def test_draft_journal_can_be_deleted_but_not_posted(accounts):
    """DRAFT journals are uncommitted and disposable (via raw path);
    posted ones never are."""
    cash, rev = accounts
    from apps.ledger.models import LedgerEntry

    j = JournalEntry.objects.create(reference="IM-DRAFT")
    LedgerEntry.objects.create(journal=j, account=cash, side="DEBIT", amount=Decimal("5.00"))
    LedgerEntry.objects.create(journal=j, account=rev, side="CREDIT", amount=Decimal("5.00"))
    LedgerEntry.objects.filter(journal=j).delete()
    JournalEntry.objects.filter(pk=j.pk).delete()
    assert not JournalEntry.objects.filter(reference="IM-DRAFT").exists()


def test_double_reversal_rejected(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "IM-DREV")
    ledger.reverse_journal(j)
    with pytest.raises(ValueError, match="already reversed"):
        ledger.reverse_journal(j)


def test_reverse_draft_rejected(accounts):
    cash, rev = accounts
    j = JournalEntry.objects.create(reference="IM-RDRAFT")
    with pytest.raises(ValueError, match="posted"):
        ledger.reverse_journal(j)


def test_admin_is_fully_read_only_for_journals(accounts):
    cash, rev = accounts
    j = _post(cash, rev, "IM-ADMIN")
    site = AdminSite()
    model_admin = JournalEntryAdmin(JournalEntry, site)
    assert model_admin.has_add_permission(object()) is False
    assert model_admin.has_change_permission(object(), j) is False
    assert model_admin.has_delete_permission(object(), j) is False
