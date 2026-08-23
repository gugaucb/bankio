from django.core.management.base import BaseCommand

from apps.ledger import reconciliation


class Command(BaseCommand):
    help = "Reconcile ledger-derived balances against operational projections."

    def add_arguments(self, parser):
        parser.add_argument("--fail-exit", action="store_true",
                            help="Exit non-zero when reconciliation fails.")

    def handle(self, *args, **opts):
        report = reconciliation.run()
        self.stdout.write(f"Accounts checked: {report['accounts_checked']}")
        self.stdout.write(f"Balanced journals: {report['balanced_journals']}")
        self.stdout.write(f"Differences: {len(report['differences'])}")
        for d in report["differences"]:
            self.stdout.write(self.style.WARNING(f"  - {d}"))
        status = report["status"]
        style = self.style.SUCCESS if status == reconciliation.RECONCILED else self.style.ERROR
        self.stdout.write(style(f"Status: {status}"))
        if opts["fail_exit"] and status == reconciliation.FAILED:
            self.exit_code = 3
