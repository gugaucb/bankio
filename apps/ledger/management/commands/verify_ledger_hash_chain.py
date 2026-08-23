from django.core.management.base import BaseCommand

from apps.ledger import canonical
from apps.ledger.models import JournalEntry


class Command(BaseCommand):
    help = "Verify the ledger hash chain and report the first invalid journal."

    def handle(self, *args, **opts):
        previous = canonical.GENESIS_HASH
        failures = 0
        checked = 0
        for j in JournalEntry.objects.filter(status="POSTED").order_by("id"):
            checked += 1
            expected_ph = canonical.payload_hash(j)
            problems = []
            if j.payload_hash != expected_ph:
                problems.append(f"payload_hash expected={expected_ph} actual={j.payload_hash}")
            if j.previous_entry_hash != previous:
                problems.append(
                    f"previous_entry_hash expected={previous} actual={j.previous_entry_hash}"
                )
            expected_chain = canonical.chain_hash(j.previous_entry_hash or "", j.payload_hash or "")
            if j.chain_hash != expected_chain:
                problems.append(f"chain_hash expected={expected_chain} actual={j.chain_hash}")
            if problems:
                failures += 1
                self.stdout.write(self.style.ERROR(
                    f"INVALID at position {checked} (journal {j.reference}, id {j.pk}): "
                    + "; ".join(problems)
                ))
                break
            previous = j.chain_hash
        self.stdout.write(f"Checked: {checked} posted journals")
        if failures:
            self.stdout.write(self.style.ERROR("RESULT: HASH CHAIN INVALID"))
            self.exit_code = 4
        else:
            self.stdout.write(self.style.SUCCESS("RESULT: HASH CHAIN VALID"))
