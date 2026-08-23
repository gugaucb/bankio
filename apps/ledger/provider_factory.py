"""Provider selection driven by settings — the ledger never imports a
concrete blockchain SDK directly."""
from django.conf import settings

from .anchor_service import SimulatedBlockchainAnchorProvider
from .anchors import BlockchainAnchorProvider


def get_provider() -> BlockchainAnchorProvider:
    name = getattr(settings, "LEDGER_ANCHOR_PROVIDER", "simulated")
    if name == "simulated":
        return SimulatedBlockchainAnchorProvider()
    if name == "external":
        # A real adapter (e.g. Bitcoin OP_RETURN, OpenTimestamps) implements
        # the same BlockchainAnchorProvider interface and is registered here.
        raise AnchorConfigError(
            "No external anchor adapter installed; implement BlockchainAnchorProvider "
            "and register it in apps/ledger/provider_factory.py"
        )
    raise AnchorConfigError(f"Unknown LEDGER_ANCHOR_PROVIDER {name!r}")


class AnchorConfigError(Exception):
    pass
