"""Idempotent initial-admin bootstrap driven by environment variables.

Usage (explicit, never automatic at startup):
    docker compose run --rm web python manage.py bootstrap_admin

Reads BANKIO_ADMIN_USERNAME / BANKIO_ADMIN_EMAIL / BANKIO_ADMIN_PASSWORD
(or *_FILE variants for Docker Secrets). The password is never logged or
stored in plaintext. An existing account with the same username is left
untouched — bootstrap does not reset credentials.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.identity.models import Role


def _secret(name):
    value = os.environ.get(name, "").strip()
    if value:
        return value
    file_path = os.environ.get(f"{name}_FILE", "").strip()
    if file_path:
        with open(file_path) as fh:
            content = fh.read().strip()
        if content:
            return content
    raise CommandError(f"{name} is required (set {name} or {name}_FILE)")


class Command(BaseCommand):
    help = "Create the initial administrator from BANKIO_ADMIN_* environment variables."

    def handle(self, *args, **options):
        User = get_user_model()
        username = _secret("BANKIO_ADMIN_USERNAME")
        email = os.environ.get("BANKIO_ADMIN_EMAIL", "").strip() or f"{username}@bankio.local"
        password = _secret("BANKIO_ADMIN_PASSWORD")

        existing = User.objects.filter(username=username).first()
        if existing is not None:
            self.stdout.write(f"User '{username}' already exists — leaving credentials untouched.")
            return

        User.objects.create_user(
            username=username,
            email=email,
            password=password,  # hashed by the user manager; never logged
            first_name="Bankio",
            last_name="Admin",
            role=Role.ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        # No secret material in output: report only the account name.
        self.stdout.write(self.style.SUCCESS(f"Administrator '{username}' created."))
