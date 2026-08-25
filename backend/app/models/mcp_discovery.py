"""MCP server discovery — collector → correlation → scoring pipeline (M3 of the AISPM port, #241).

Ports `app/discover/mcp/` + `lib/mcp-discovery-data.ts` from the
ai-spm-dashboard prototype, where collector "observations" (evidence that an
MCP server exists, from a given source — IDE config, network traffic,
browser extension, host process list) are correlated by a normalized
name+host key and scored by how many independent sources corroborate the
same server. Here that pipeline lands in a real, tenant-scoped
`DiscoveredMcpServer` row per correlated candidate rather than an in-memory
store — see `services/mcp_discovery_service.py` for `correlate()` /
`score_server()`.

A scan is idempotent: re-running collectors re-correlates and re-scores,
upserting by `(tenant_id, canonical_key)`. `sources` accumulates every
distinct collector source seen for a candidate; a human-set `status`
(promoted/dismissed) survives re-scans, matching `agent_discovery`'s
convention (#233).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin

# JSONB on PostgreSQL (GIN-indexable), falls back to JSON on SQLite (test suite).
_JSONBColumn = JSON().with_variant(JSONB(), "postgresql")


class McpDiscoveryStatus(str, enum.Enum):
    candidate = "candidate"
    promoted = "promoted"
    dismissed = "dismissed"


class DiscoveredMcpServer(GRCBase, TenantScopedMixin):
    """One MCP server candidate, fused from one or more collector observations."""

    __tablename__ = "discovered_mcp_servers"
    __table_args__ = (
        Index("ix_grc_discovered_mcp_servers_tenant_status", "tenant_id", "status"),
        # A scan re-correlating the same (name, host) upserts this row
        # instead of duplicating it — see services.mcp_discovery_service.run_scan.
        UniqueConstraint(
            "tenant_id",
            "canonical_key",
            name="uq_discovered_mcp_server_canonical_key",
        ),
        {"schema": "grc"},
    )

    # Normalized "name@host" used to correlate observations across
    # collectors — see services.mcp_discovery_service.canonical_key().
    canonical_key: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    transport: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="stdio",
        comment="stdio, http, sse",
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Distinct collector sources that have observed this server, e.g.
    # ["ide_config", "network_traffic"] — drives score_server().
    sources: Mapped[list[str]] = mapped_column(_JSONBColumn, nullable=False, default=list)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[McpDiscoveryStatus] = mapped_column(
        Enum(McpDiscoveryStatus, schema="grc", name="mcp_discovery_status"),
        nullable=False,
        default=McpDiscoveryStatus.candidate,
    )
    # Set once promote() copies this candidate into the AI inventory.
    promoted_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.ai_assets.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
