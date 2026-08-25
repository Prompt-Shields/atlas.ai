"""#233 · Agent discovery collectors — seed data + feature flag."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.models.agent_discovery import AgentCloudProvider
from app.services.agent_discovery_collectors import (
    LiveAgentCollector,
    get_collectors,
    live_collectors_enabled,
)

pytestmark = pytest.mark.unit


async def test_seed_collectors_cover_all_providers():
    collectors = get_collectors()
    providers = {c.provider for c in collectors}
    assert providers == set(AgentCloudProvider)


async def test_seed_collector_returns_deterministic_agents():
    collectors = {c.provider: c for c in get_collectors()}
    first = await collectors[AgentCloudProvider.aws_bedrock_agentcore].collect()
    second = await collectors[AgentCloudProvider.aws_bedrock_agentcore].collect()
    assert first == second
    assert len(first) > 0
    assert all(a.external_id for a in first)


async def test_live_collectors_disabled_by_default():
    assert live_collectors_enabled() is False
    assert not any(isinstance(c, LiveAgentCollector) for c in get_collectors())


async def test_live_collector_raises_not_implemented():
    collector = LiveAgentCollector(AgentCloudProvider.azure_ai_foundry)
    with pytest.raises(NotImplementedError):
        await collector.collect()


async def test_live_collectors_enabled_flag_included_when_on(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_discovery_live_collectors_enabled", True)
    collectors = get_collectors()
    assert any(isinstance(c, LiveAgentCollector) for c in collectors)
