"""
Database-level immutability for posted journal rows themselves.

Task 02 triggers protect ledger *entries* and gate the DRAFT->POSTED
transition. This migration protects the *journal row*: once POSTED, no
field except the `reverses` link may change, and the row can never be
deleted - regardless of ORM save(), queryset .update(), raw SQL or admin.
"""

from django.db import migrations

JOURNAL_IMMUTABILITY_TRIGGER = """
CREATE OR REPLACE FUNCTION bankio_protect_posted_journal() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Posted journal % cannot be deleted', OLD.id;
        RETURN OLD;
    END IF;
    IF ROW(OLD.reference, OLD.description, OLD.status, OLD.posted_at, OLD.currency)
       IS DISTINCT FROM
       ROW(NEW.reference, NEW.description, NEW.status, NEW.posted_at, NEW.currency) THEN
        RAISE EXCEPTION 'Posted journal % is immutable', OLD.id;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_posted_journal_immutable_update
BEFORE UPDATE ON ledger_journalentry
FOR EACH ROW WHEN (OLD.status = 'POSTED')
EXECUTE FUNCTION bankio_protect_posted_journal();

CREATE TRIGGER trg_posted_journal_immutable_delete
BEFORE DELETE ON ledger_journalentry
FOR EACH ROW WHEN (OLD.status = 'POSTED')
EXECUTE FUNCTION bankio_protect_posted_journal();
"""

DROP_SQL = """
DROP TRIGGER IF EXISTS trg_posted_journal_immutable_update ON ledger_journalentry;
DROP TRIGGER IF EXISTS trg_posted_journal_immutable_delete ON ledger_journalentry;
DROP FUNCTION IF EXISTS bankio_protect_posted_journal();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0003_journalentry_currency_ledgeraccount_status_and_more"),
    ]

    operations = [
        migrations.RunSQL(JOURNAL_IMMUTABILITY_TRIGGER, DROP_SQL),
    ]
