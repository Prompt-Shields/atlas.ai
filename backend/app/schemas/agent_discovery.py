"""Pydantic schemas for the agent-discovery router (#233).

Wire format for `app.models.agent_discovery.DiscoveredAgent`. Enum values
mirror the ORM enums (snake_case) so the wire contract stays in lockstep
with the DB, matching `schemas/saas_vendor.py`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.agent_discovery import AgentCloudProvider, AgentDiscoveryStatus


class DiscoveredAgentResponse(BaseModel):
    """A discovered agent as returned by the API."""

    id: str
    provider: AgentCloudProvider
    external_id: str
    name: str
    registry: str
    runtime: str | None = None
    region: str | None = None
    description: str | None = None
    status: AgentDiscoveryStatus
    owner_user_id: str | None = None
    last_seen_at: datetime

    class Config:
        from_attributes = True


class AgentDiscoveryListResponse(BaseModel):
    """Discovered agents grouped by status, as the Agent Discovery page needs them."""

    shadow: list[DiscoveredAgentResponse] = Field(default_factory=list)
    pending: list[DiscoveredAgentResponse] = Field(default_factory=list)
    approved: list[DiscoveredAgentResponse] = Field(default_factory=list)
    dismissed: list[DiscoveredAgentResponse] = Field(default_factory=list)


class AgentDiscoveryScanResponse(BaseModel):
    """Result of a scan: how many agents were touched, plus the current set."""

    scanned: int
    created: int
    updated: int
    agents: list[DiscoveredAgentResponse]


class AgentStatusUpdate(BaseModel):
    """Approve / dismiss (or reset) a discovered agent."""

    status: AgentDiscoveryStatus
