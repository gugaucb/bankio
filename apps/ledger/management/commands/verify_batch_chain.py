from django.core.management.base import BaseCommand

from apps.ledger import canonical
from apps.ledger.models import LedgerProofBatch
from apps.ledger.proof_batches import verify_batch_signature


class Command(BaseCommand):
    help = "Verify continuity and signatures across sealed proof batches."

    def handle(self, *args, **opts):
        previous_hash = canonical.GENESIS_HASH
        expected_sequence = 1
        failures = 0
        checked = 0
        for batch in LedgerProofBatch.objects.filter(
            status__in=[LedgerProofBatch.Status.SEALED,
                        LedgerProofBatch.Status.ANCHORED,
                        LedgerProofBatch.Status.VERIFIED]
        ).order_by("sequence"):
            checked += 1
            problems = []
            if batch.sequence != expected_sequence:
                problems.append(f"sequence gap: expected {expected_sequence}")
            if batch.previous_batch_hash != previous_hash:
                problems.append(
                    f"previous_batch_hash mismatch: expected {previous_hash}, "
                    f"actual {batch.previous_batch_hash}"
                )
            if not verify_batch_signature(batch):
                problems.append("invalid manifest signature")
            if problems:
                failures += 1
                self.stdout.write(self.style.ERROR(
                    f"INVALID batch #{batch.sequence} (id {batch.pk}): " + "; ".join(problems)
                ))
                break
            previous_hash = batch.batch_manifest_hash
            expected_sequence += 1

        self.stdout.write(f"Checked: {checked} sealed batches")
        if failures:
            self.stdout.write(self.style.ERROR("RESULT: BATCH CHAIN INVALID"))
            self.exit_code = 5
        else:
            self.stdout.write(self.style.SUCCESS("RESULT: BATCH CHAIN VALID"))
