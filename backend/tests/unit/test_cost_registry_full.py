"""Importing ``sync_service`` must wire EVERY cost provider into the registry.

The orchestrator imports each per-vendor adapter for its import-time
registration side effect. This guards against a provider being added to the
``CostProvider`` enum (or a new adapter being written) without its connector
being imported in ``sync_service`` — which would otherwise surface only as a
runtime ``KeyError`` during a real sync.

The autouse ``_restore_cost_connector_registry`` fixture in ``conftest.py``
snapshots and restores the global registry around each test, so importing the
adapters here does not leak registrations into other tests.
"""

from __future__ import annotations

from app.models.ai_cost_record import CostProvider


def test_sync_service_registers_every_cost_provider():
    # Importing the orchestrator triggers each adapter's self-registration.
    import app.services.cost.sync_service  # noqa: F401
    from app.services.cost import registry

    assert registry.registered_providers() == set(CostProvider)
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

    for provider in CostProvider:
        connector = registry.get_connector(provider)
        assert connector.provider is provider
