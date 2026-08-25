"""Agent discovery — cloud agent registry scanner (M1 of the AISPM port, #233).

Ports `app/agent-discovery/page.tsx` + `lib/agent-discovery/*` from the
ai-spm-dashboard prototype. There, agents are seeded/simulated collector
output held in an in-memory store; here a `DiscoveredAgent` row is the
durable, tenant-scoped record of one agent seen in a cloud registry (AWS
Bedrock AgentCore, Azure AI Foundry, GCP Knowledge Catalog).

A scan is idempotent: collectors are re-run and their output is upserted by
`(tenant_id, provider, external_id)` (see the unique constraint below and
`services/agent_discovery_service.run_scan`). Newly-seen agents land in
`shadow`; a human moves them to `approved`/`dismissed` via the PATCH route.
`pending` is available for a future approval-workflow state.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
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


class AgentCloudProvider(str, enum.Enum):
    aws_bedrock_agentcore = "aws_bedrock_agentcore"
    azure_ai_foundry = "azure_ai_foundry"
    gcp_knowledge_catalog = "gcp_knowledge_catalog"


class AgentDiscoveryStatus(str, enum.Enum):
    shadow = "shadow"
    pending = "pending"
    approved = "approved"
    dismissed = "dismissed"


class DiscoveredAgent(GRCBase, TenantScopedMixin):
    """One agent observed in a cloud registry scan."""

    __tablename__ = "discovered_agents"
    __table_args__ = (
        Index("ix_grc_discovered_agents_tenant_status", "tenant_id", "status"),
        # A scan re-observing the same agent upserts this row instead of
        # duplicating it — see services.agent_discovery_service.run_scan.
        UniqueConstraint(
            "tenant_id",
            "provider",
            "external_id",
            name="uq_discovered_agent_provider_external_id",
        ),
        {"schema": "grc"},
    )

    provider: Mapped[AgentCloudProvider] = mapped_column(
        Enum(AgentCloudProvider, schema="grc", name="agent_cloud_provider"),
        nullable=False,
    )
    # The provider's own id for the agent (ARN, resource id, catalog entry id).
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # The specific registry/catalog the agent was found in, e.g. "Bedrock AgentCore".
    registry: Mapped[str] = mapped_column(String(255), nullable=False)
    # The runtime hosting the agent, e.g. "bedrock-agent-runtime" — nullable, not
    # every registry entry reports one.
    runtime: Mapped[str | None] = mapped_column(String(255), nullable=True)
    region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AgentDiscoveryStatus] = mapped_column(
        Enum(AgentDiscoveryStatus, schema="grc", name="agent_discovery_status"),
        nullable=False,
        default=AgentDiscoveryStatus.shadow,
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Raw collector payload, kept for audit/debugging — not surfaced in the API.
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(_JSONBColumn, nullable=False, default=dict)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
