"""Developer API enablement — custom event definitions and ingestion records.

This module supports the "Developer API" surface that allows tenant-side
engineers to:

  1. Define their own event types (DeveloperEventDefinition) — e.g.
     "agent.task_started", "rag.retrieval_completed", "guardrail.triggered".
  2. Send events tagged with those types via the /v1/developer/events
     endpoint, authenticated with a tenant-scoped API key.
  3. Review ingested events on the Atlas AI dashboard.

The existing APIKey model from app.models.user is reused. We extend
permission with a scopes column via the DeveloperAPIKeyScope side table,
so we don't have to migrate the existing api_keys schema.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
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

# JSON type — JSONB on PostgreSQL (with GIN-indexable querying), generic
# JSON on SQLite (used by the test suite).
JSONColumn = JSON().with_variant(JSONB(), "postgresql")

# ──────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────


class DeveloperScope(str, enum.Enum):
    """Permissions an API key can be granted on the developer API."""

    EVENTS_WRITE = "events:write"  # ingest events
    EVENTS_READ = "events:read"  # read own tenant's events
    EVENT_TYPES_WRITE = "event_types:write"  # create/edit event definitions
    EVENT_TYPES_READ = "event_types:read"


class EventSeverity(str, enum.Enum):
    """Optional severity attached to an ingested event."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# ──────────────────────────────────────────────────────────────────────
# DeveloperAPIKeyScope — side table on existing api_keys
# ──────────────────────────────────────────────────────────────────────


class DeveloperAPIKeyScope(GRCBase):
    """Permission scopes for an existing api_keys row.

    Joins to grc.api_keys without modifying the api_keys schema. One
    api_key can hold multiple scopes; deletion of the api_key cascades.
    """

    __tablename__ = "developer_api_key_scopes"

    api_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.api_keys.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scope: Mapped[DeveloperScope] = mapped_column(
        Enum(DeveloperScope, schema="grc", name="developer_scope"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("api_key_id", "scope", name="uq_api_key_scope"),
        {"schema": "grc"},
    )


# ──────────────────────────────────────────────────────────────────────
# DeveloperEventDefinition — user-defined event types
# ──────────────────────────────────────────────────────────────────────


class DeveloperEventDefinition(GRCBase, TenantScopedMixin):
    """A tenant-defined event type developers can emit.

    Example:
        name="agent.task_started"
        description="Fired when an autonomous agent picks up a task"
        schema={"task_id": "string", "agent_id": "string"}

    Names must be unique within a tenant. The schema is advisory only —
    /v1/developer/events accepts any JSON payload. It's stored so the UI
    can render expected fields and warn about missing ones.
    """

    __tablename__ = "developer_event_definitions"

    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    schema: Mapped[dict | None] = mapped_column(JSONColumn, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_severity: Mapped[EventSeverity] = mapped_column(
        Enum(EventSeverity, schema="grc", name="event_severity"),
        default=EventSeverity.INFO,
        nullable=False,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_event_def_tenant_name"),
        Index("ix_event_defs_tenant_enabled", "tenant_id", "is_enabled"),
        {"schema": "grc"},
    )


# ──────────────────────────────────────────────────────────────────────
# DeveloperEvent — ingested events
# ──────────────────────────────────────────────────────────────────────


class DeveloperEvent(GRCBase, TenantScopedMixin):
    """An event ingested via /v1/developer/events.

    Append-only. Indexed for the dashboard review view: list newest events
    for a tenant, filter by type/severity, paginate by id.
    """

    __tablename__ = "developer_events"

    event_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    severity: Mapped[EventSeverity] = mapped_column(
        Enum(EventSeverity, schema="grc", name="event_severity"),
        default=EventSeverity.INFO,
        nullable=False,
        index=True,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
        comment="When the event happened on the client (may differ from created_at).",
    )

    # Payload — fully opaque JSON
    payload: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)

    # Common discoverability fields, lifted out of payload for indexing
    source: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    user_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)

    # Provenance
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.api_keys.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Counts — if a single ingest record represents a roll-up (e.g.
    # client-side batching collapses 100 identical events into one),
    # use occurrences > 1.
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __table_args__ = (
        Index(
            "ix_dev_events_tenant_occurred",
            "tenant_id",
            "occurred_at",
            postgresql_using="btree",
        ),
        Index(
            "ix_dev_events_tenant_type_occurred",
            "tenant_id",
            "event_type",
            "occurred_at",
        ),
        Index(
            "ix_dev_events_tenant_severity",
            "tenant_id",
            "severity",
        ),
        {"schema": "grc"},
    )
