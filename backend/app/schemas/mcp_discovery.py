"""Pydantic schemas for the MCP discovery router (#241).

Wire format for `app.models.mcp_discovery.DiscoveredMcpServer`. Enum values
mirror the ORM enum (snake_case) so the wire contract stays in lockstep
with the DB, matching `schemas/agent_discovery.py`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.mcp_discovery import McpDiscoveryStatus


class DiscoveredMcpServerResponse(BaseModel):
    """A discovered MCP server candidate as returned by the API."""

    id: str
    canonical_key: str
    name: str
    host: str | None = None
    transport: str
    description: str | None = None
    sources: list[str]
    observation_count: int
    confidence_score: float
    status: McpDiscoveryStatus
    promoted_asset_id: str | None = None
    last_observed_at: datetime

    class Config:
        from_attributes = True


class McpDiscoveryListResponse(BaseModel):
    """Discovered MCP server candidates, scored and ready to render."""

    servers: list[DiscoveredMcpServerResponse] = Field(default_factory=list)


class McpDiscoveryScanResponse(BaseModel):
    """Result of a scan: raw observations gathered plus the current candidate set."""

    scanned_observations: int
    created: int
    updated: int
    servers: list[DiscoveredMcpServerResponse]


class McpServerStatusUpdate(BaseModel):
    """Dismiss (or reset) a discovered MCP server."""

    status: McpDiscoveryStatus


class McpServerPromoteResponse(BaseModel):
    """Result of promoting a candidate into the AI inventory."""

    server: DiscoveredMcpServerResponse
    asset_id: str
