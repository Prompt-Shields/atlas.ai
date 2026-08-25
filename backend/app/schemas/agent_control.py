"""Pydantic schemas for the agent-control router (#235).

Wire format for the control-tower view: `app.models.agent_discovery.DiscoveredAgent`
enriched with derived health + lifecycle, and the persisted overrides from
`app.models.agent_control.AgentControlState`. See
`services/agent_control_service.py` for how the two are combined.
"""

from __future__ import annotations

import enum
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.agent_control import AgentControlAction
from app.models.agent_discovery import AgentCloudProvider, AgentDiscoveryStatus


class AgentHealth(str, enum.Enum):
    """Derived from `DiscoveredAgent.last_seen_at` recency — not persisted."""

    healthy = "healthy"
    degraded = "degraded"
    unhealthy = "unhealthy"


class AgentLifecycle(str, enum.Enum):
    """Discovery status, overridden by a persisted pause/quarantine action."""

    unmonitored = "unmonitored"
    provisioning = "provisioning"
    active = "active"
    paused = "paused"
    quarantined = "quarantined"


class AgentControlStateResponse(BaseModel):
    """One governed agent's control-tower view: discovery identity + derived/persisted state."""

    id: str
    provider: AgentCloudProvider
    name: str
    registry: str
    discovery_status: AgentDiscoveryStatus
    health: AgentHealth
    lifecycle: AgentLifecycle
    guardrail_enabled: bool
    last_action: AgentControlAction | None = None
    last_action_at: datetime | None = None
    last_seen_at: datetime


class AgentControlListResponse(BaseModel):
    """All governed agents for the tenant, control-tower enriched."""

    agents: list[AgentControlStateResponse] = Field(default_factory=list)


class AgentControlActionRequest(BaseModel):
    """Body of `POST /agent-control/{id}/action`."""

    action: AgentControlAction
