"""Agent discovery collectors — one per cloud registry (#233).

The ai-spm-dashboard prototype simulates collectors with deterministic
seeded data (no real cloud calls). This module keeps that shape: a
`SeedAgentCollector` per provider always runs; real SDK-backed collectors
(boto3 for Bedrock AgentCore, the Azure AI Foundry SDK, GCP's Knowledge
Catalog API) are a follow-up — `live_collectors_enabled()` is the switch a
future change flips once credentials/SDKs are wired, and
`LiveAgentCollector` documents the contract they'll implement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import get_settings
from app.models.agent_discovery import AgentCloudProvider


@dataclass(frozen=True)
class CollectedAgent:
    """One agent as reported by a collector, before it's persisted."""

    external_id: str
    name: str
    registry: str
    runtime: str | None = None
    region: str | None = None
    description: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class AgentCollector(Protocol):
    """A source of `CollectedAgent`s for one cloud provider."""

    provider: AgentCloudProvider

    async def collect(self) -> list[CollectedAgent]: ...


class LiveAgentCollector:
    """Placeholder for a real SDK-backed collector — not yet implemented.

    Gated by `live_collectors_enabled()` so it's never invoked by default;
    wiring boto3 / azure-ai-foundry / google-cloud-datacatalog is tracked as
    follow-up work (see the PR for #233).
    """

    def __init__(self, provider: AgentCloudProvider) -> None:
        self.provider = provider

    async def collect(self) -> list[CollectedAgent]:
        raise NotImplementedError(
            f"Live collector for {self.provider.value} is not implemented yet"
        )


class SeedAgentCollector:
    """Deterministic fixture data for one provider (ported from AISPM's seed)."""

    def __init__(self, provider: AgentCloudProvider, agents: list[CollectedAgent]) -> None:
        self.provider = provider
        self._agents = agents

    async def collect(self) -> list[CollectedAgent]:
        return list(self._agents)


_SEED_AGENTS: dict[AgentCloudProvider, list[CollectedAgent]] = {
    AgentCloudProvider.aws_bedrock_agentcore: [
        CollectedAgent(
            external_id="arn:aws:bedrock-agentcore:us-east-1:agent/support-triage",
            name="Support Triage Agent",
            registry="Bedrock AgentCore",
            runtime="bedrock-agent-runtime",
            region="us-east-1",
            description="Classifies and routes inbound support tickets.",
        ),
        CollectedAgent(
            external_id="arn:aws:bedrock-agentcore:us-east-1:agent/sales-copilot",
            name="Sales Copilot",
            registry="Bedrock AgentCore",
            runtime="bedrock-agent-runtime",
            region="us-east-1",
            description="Drafts outreach emails from CRM context.",
        ),
    ],
    AgentCloudProvider.azure_ai_foundry: [
        CollectedAgent(
            external_id="/subscriptions/foundry/agents/contract-reviewer",
            name="Contract Reviewer",
            registry="Azure AI Foundry",
            runtime="azure-ai-foundry-runtime",
            region="westeurope",
            description="Flags non-standard clauses in vendor contracts.",
        ),
    ],
    AgentCloudProvider.gcp_knowledge_catalog: [
        CollectedAgent(
            external_id="projects/atlas-demo/agents/data-steward",
            name="Data Steward Agent",
            registry="GCP Knowledge Catalog",
            runtime="vertex-ai-agent-runtime",
            region="europe-west1",
            description="Tags and classifies newly-registered datasets.",
        ),
    ],
}


def live_collectors_enabled() -> bool:
    """Feature flag for real SDK-backed collectors (default off, see above)."""
    return get_settings().agent_discovery_live_collectors_enabled


def get_collectors() -> list[AgentCollector]:
    """The collectors a scan should run: seed always, live ones behind the flag."""
    collectors: list[AgentCollector] = [
        SeedAgentCollector(provider, agents) for provider, agents in _SEED_AGENTS.items()
    ]
    if live_collectors_enabled():
        collectors.extend(LiveAgentCollector(provider) for provider in AgentCloudProvider)
    return collectors
