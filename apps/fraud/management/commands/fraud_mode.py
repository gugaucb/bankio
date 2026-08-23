"""Controlled engine-mode switching (spec PART 7 / gate tasks).

GET is open to any staff reader; SET enforces the manage_policies
permission inside modes.set_mode and is always audited.
"""
from django.core.management.base import BaseCommand, CommandError

from apps.fraud.modes import get_mode, set_mode


class Command(BaseCommand):
    help = "Get or set the fraud engine mode (DISABLED/SHADOW/CHALLENGE_ONLY/ENFORCEMENT)"

    def add_arguments(self, parser):
        parser.add_argument("mode", nargs="?", default=None)
        parser.add_argument("--actor-id", type=int, default=None,
                            help="User pk of the operator (audited); omit to read only")

    def handle(self, *args, **opts):
        if not opts["mode"]:
            self.stdout.write(f"fraud_mode={get_mode()}")
            return
        actor_id = opts["actor_id"]
        if actor_id is None:
            raise CommandError("Setting the mode requires --actor-id (audited operation)")
        from apps.identity.models import User

        try:
            actor = User.objects.get(pk=actor_id)
        except User.DoesNotExist:
            raise CommandError("unknown actor")
        try:
            set_mode(opts["mode"], actor=actor)
        except Exception as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"fraud_mode={get_mode()}"))
