from django.core.management.base import BaseCommand

from apps.audit.services import record as audit
from apps.cards.billing import close_card_statements, notify_overdue_statements


class Command(BaseCommand):
    help = "Close the previous month's billing cycle for every credit card (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--reference", default=None,
                            help="Optional YYYY-MM-DD reference date (defaults to today).")

    def handle(self, *args, **options):
        ref = options.get("reference")
        if ref:
            y, m, d = (int(p) for p in ref.split("-"))
            from datetime import date as _date
            ref = _date(y, m, d)
        created = close_card_statements(reference=ref)
        for stmt in created:
            audit(action="CARD_INVOICE_CLOSED", resource=stmt.card,
                  metadata={"statement": stmt.pk,
                            "period_end": str(stmt.period_end),
                            "amount_due": str(stmt.amount_due),
                            "due_date": str(stmt.due_date)})
        self.stdout.write(self.style.SUCCESS(
            f"Closed {len(created)} statement(s)."))
        notify_overdue_statements()
