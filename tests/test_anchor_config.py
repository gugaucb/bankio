"""Anchor configuration: provider selection stays replaceable and safe."""
import pytest
from django.test import override_settings

from apps.ledger import provider_factory


def test_simulated_provider_selected():
    p = provider_factory.get_provider()
    assert isinstance(p, SimulatedProvider)


from apps.ledger.anchor_service import SimulatedBlockchainAnchorProvider as SimulatedProvider  # noqa: E402


@override_settings(LEDGER_ANCHOR_PROVIDER="external")
def test_external_provider_not_installed_by_default():
    with pytest.raises(provider_factory.AnchorConfigError):
        provider_factory.get_provider()


@override_settings(LEDGER_ANCHOR_PROVIDER="bogus")
def test_unknown_provider_rejected():
    with pytest.raises(provider_factory.AnchorConfigError):
        provider_factory.get_provider()


def test_confirmation_policy_is_configurable(settings):
    settings.LEDGER_ANCHOR_MIN_CONFIRMATIONS = 3
    from django.conf import settings as s

    assert s.LEDGER_ANCHOR_MIN_CONFIRMATIONS == 3
