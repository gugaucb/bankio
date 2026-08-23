from django.core.management.base import BaseCommand

from apps.ledger import anchor_service
from apps.ledger.models import LedgerProofBatch


class Command(BaseCommand):
    help = "Anchor sealed ledger batches externally (asynchronous, non-blocking for banking)."

    def handle(self, *args, **opts):
        # 1. submit new batches
        pending = LedgerProofBatch.objects.filter(status=LedgerProofBatch.Status.SEALED)
        submitted = 0
        for batch in pending:
            if not batch.anchors.exists():
                anchor_service.anchor_batch(batch)
                submitted += 1

        # 2. poll in-flight anchors until confirmed or exhausted polls
        confirmed = 0
        from apps.ledger.models import LedgerAnchor

        for anchor in LedgerAnchor.objects.filter(
            status__in=[LedgerAnchor.Status.SUBMITTED, LedgerAnchor.Status.CONFIRMING]
        ):
            anchor_service.confirm_anchor(anchor)
            if anchor.status == LedgerAnchor.Status.CONFIRMED:
                confirmed += 1

        self.stdout.write(f"Submitted: {submitted}, newly confirmed: {confirmed}")
