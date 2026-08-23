"""
Database-level integrity guarantees for the ledger.

Row-level CHECK constraints reject invalid entries outright.
PostgreSQL triggers enforce cross-row invariants that CHECK constraints
cannot express:

1. A journal may only become POSTED if debits == credits (and total > 0).
2. Posted journals are immutable: no inserts, updates or deletes on their
   ledger entries, regardless of the code path used.

These hold even when bypassing the Django ORM (raw SQL, queryset .update(),
bulk operations, admin actions).
"""

from django.db import migrations, models

BALANCED_TRIGGER = """
CREATE OR REPLACE FUNCTION bankio_check_journal_balanced() RETURNS trigger AS $$
DECLARE
    d NUMERIC;
    c NUMERIC;
BEGIN
    SELECT COALESCE(SUM(amount) FILTER (WHERE side = 'DEBIT'), 0),
           COALESCE(SUM(amount) FILTER (WHERE side = 'CREDIT'), 0)
      INTO d, c
      FROM ledger_ledgerentry
     WHERE journal_id = NEW.id;
    IF d <> c OR d = 0 THEN
        RAISE EXCEPTION 'Journal % unbalanced: debits=% credits=%', NEW.id, d, c;
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_journal_posted_balanced
BEFORE UPDATE OF status ON ledger_journalentry
FOR EACH ROW WHEN (NEW.status = 'POSTED')
EXECUTE FUNCTION bankio_check_journal_balanced();
"""

IMMUTABLE_ENTRIES_TRIGGER = """
CREATE OR REPLACE FUNCTION bankio_protect_posted_entries() RETURNS trigger AS $$
BEGIN
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        IF EXISTS (SELECT 1 FROM ledger_journalentry j WHERE j.id = NEW.journal_id AND j.status = 'POSTED')
           AND (TG_OP = 'INSERT' OR OLD.* IS DISTINCT FROM NEW.*) THEN
            RAISE EXCEPTION 'Ledger entries of posted journal % are immutable', NEW.journal_id;
        END IF;
        RETURN NEW;
    ELSE
        IF EXISTS (SELECT 1 FROM ledger_journalentry j WHERE j.id = OLD.journal_id AND j.status = 'POSTED') THEN
            RAISE EXCEPTION 'Ledger entries of posted journal % cannot be deleted', OLD.journal_id;
        END IF;
        RETURN OLD;
    END IF;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_posted_entries_insert
BEFORE INSERT ON ledger_ledgerentry
FOR EACH ROW EXECUTE FUNCTION bankio_protect_posted_entries();

CREATE TRIGGER trg_protect_posted_entries_update
BEFORE UPDATE ON ledger_ledgerentry
FOR EACH ROW EXECUTE FUNCTION bankio_protect_posted_entries();

CREATE TRIGGER trg_protect_posted_entries_delete
BEFORE DELETE ON ledger_ledgerentry
FOR EACH ROW EXECUTE FUNCTION bankio_protect_posted_entries();
"""


DROP_SQL = """
DROP TRIGGER IF EXISTS trg_journal_posted_balanced ON ledger_journalentry;
DROP FUNCTION IF EXISTS bankio_check_journal_balanced();
DROP TRIGGER IF EXISTS trg_protect_posted_entries_insert ON ledger_ledgerentry;
DROP TRIGGER IF EXISTS trg_protect_posted_entries_update ON ledger_ledgerentry;
DROP TRIGGER IF EXISTS trg_protect_posted_entries_delete ON ledger_ledgerentry;
DROP FUNCTION IF EXISTS bankio_protect_posted_entries();
"""


class Migration(migrations.Migration):

    dependencies = [
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                check=models.Q(amount__gt=0), name="ledgerentry_amount_positive"
            ),
        ),
        migrations.AddConstraint(
            model_name="ledgerentry",
            constraint=models.CheckConstraint(
                check=models.Q(side__in=["DEBIT", "CREDIT"]), name="ledgerentry_side_valid"
            ),
        ),
        migrations.AddConstraint(
            model_name="journalentry",
            constraint=models.CheckConstraint(
                check=models.Q(status__in=["DRAFT", "POSTED"]), name="journalentry_status_valid"
            ),
        ),
        migrations.RunSQL(
            BALANCED_TRIGGER + IMMUTABLE_ENTRIES_TRIGGER, DROP_SQL
        ),
    ]
