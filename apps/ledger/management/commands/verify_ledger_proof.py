from django.core.management.base import BaseCommand

from apps.ledger import proof_verification


class Command(BaseCommand):
    help = "Independently verify the cryptographic proof of one historical journal."

    def add_arguments(self, parser):
        parser.add_argument("--journal", required=True, help="Journal reference")

    def handle(self, *args, **opts):
        report = proof_verification.verify_journal(opts["journal"])
        style = {
            proof_verification.VERIFIED: self.style.SUCCESS,
            proof_verification.PENDING: self.style.WARNING,
        }.get(report["result"], self.style.ERROR)
        self.stdout.write(style(proof_verification.pretty(report)))
        if report["result"] != proof_verification.VERIFIED:
            self.exit_code = 6
