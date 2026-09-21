"""Importing ``sync_service`` must wire every *pull* cost provider into the registry.

The orchestrator imports each per-vendor adapter for its import-time
registration side effect. This guards against a provider being added to the
``CostProvider`` enum (or a new adapter being written) without its connector
being imported in ``sync_service`` — which would otherwise surface only as a
runtime ``KeyError`` during a real sync.

**Pull providers only.** Cost slice 2 added three push-mode providers
(``azure_ai_foundry``, ``aws_bedrock``, ``self_hosted``) whose spend arrives by
the customer's app POSTing to /api/v1/cost/usage. They have no vendor API to
fetch from, so they deliberately have no connector, and the daily sync sweep
subtracts them for the same reason — see ``_PUSH_ONLY_COST_PROVIDERS`` in
``app/routers/cost.py``. Asserting a connector for those would demand an
adapter that cannot exist.

The distinction is derived from ``SelfHostedCostProvider`` rather than a
hand-written list, so a fourth push provider does not silently start failing
this test, and a new *pull* vendor without its adapter still does.

The autouse ``_restore_cost_connector_registry`` fixture in ``conftest.py``
snapshots and restores the global registry around each test, so importing the
adapters here does not leak registrations into other tests.
"""

from __future__ import annotations

import pytest

from app.models.ai_cost_record import CostProvider, SelfHostedCostProvider

pytestmark = [pytest.mark.unit]

# Providers whose spend is pushed to us, not pulled from a vendor API.
PUSH_ONLY: frozenset[str] = frozenset(p.value for p in SelfHostedCostProvider)

# What the registry is expected to hold: everything else.
PULL_PROVIDERS: frozenset[CostProvider] = frozenset(
    p for p in CostProvider if p.value not in PUSH_ONLY
)


def test_sync_service_registers_every_pull_cost_provider():
    # Importing the orchestrator triggers each adapter's self-registration.
    import app.services.cost.sync_service  # noqa: F401
    from app.services.cost import registry

    assert registry.registered_providers() == set(PULL_PROVIDERS)
    # Be explicit about the full set for a readable failure message.
    assert registry.registered_providers() == {
        CostProvider.anthropic,
        CostProvider.openai,
        CostProvider.cursor,
        CostProvider.github_copilot,
        CostProvider.vercel,
    }


def test_every_registered_connector_matches_its_provider_key():
    import app.services.cost.sync_service  # noqa: F401
    from app.services.cost import registry

    for provider in PULL_PROVIDERS:
        connector = registry.get_connector(provider)
        assert connector.provider is provider


def test_push_providers_have_no_connector():
    """The other half of the invariant, asserted rather than assumed.

    A connector appearing for a push provider would mean the daily sweep could
    try to sync an integration that has no credentials and no vendor endpoint,
    failing every cycle and parking the integration in ``status=ERROR`` — while
    the customer's pushes were succeeding all along.
    """
    import app.services.cost.sync_service  # noqa: F401
    from app.services.cost import registry

    for push_provider in SelfHostedCostProvider:
        provider = CostProvider(push_provider.value)
        assert provider not in registry.registered_providers(), (
            f"{provider.value} is push-mode and must not have a sync connector"
        )


def test_the_two_halves_cover_the_enum():
    """No provider falls between the two rules.

    A new enum member that is neither registered nor recognised as push-mode
    would be silently untested by both tests above; this makes that a failure.
    """
    push_as_cost = {CostProvider(p.value) for p in SelfHostedCostProvider}
    assert set(PULL_PROVIDERS) | push_as_cost == set(CostProvider)
    assert not (set(PULL_PROVIDERS) & push_as_cost)
