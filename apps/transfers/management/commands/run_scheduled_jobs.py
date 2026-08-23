"""Process due scheduled transfers and recurring payments. Run via cron."""
from django.core.management.base import BaseCommand

from apps.transfers.services import process_due_scheduled


class Command(BaseCommand):
    help = "Process due scheduled transfers / recurring items"

    def handle(self, *args, **options):
        processed = process_due_scheduled()
        self.stdout.write(f"Processed: {processed}")
