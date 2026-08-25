"""PromptEvent — hash-only prompt telemetry from the prompt-shields
clients (Safari extension, macOS widget, Python SDK).

One row per client-side event (or rollup — `occurrences` carries the
count for clients that batch, e.g. the macOS widget's daily usage
rollups). Append-only; never updated after insert.

PRIVACY: deliberately no prompt-text column. See the spec —
docs/superpowers/specs/2026-06-11-prompt-telemetry-design.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin
from app.models.developer import JSONColumn
from app.schemas.telemetry import (
    PromptEventAction,
    PromptEventKind,
    PromptEventSeverity,
    PromptEventSource,
)


class PromptEvent(GRCBase, TenantScopedMixin):
    __tablename__ = "prompt_events"
    __table_args__ = (
        Index("ix_grc_prompt_events_tenant_occurred", "tenant_id", "occurred_at"),
        Index(
            "ix_grc_prompt_events_tenant_source_occurred",
            "tenant_id",
            "source",
            "occurred_at",
        ),
        Index(
            "ix_grc_prompt_events_tenant_app_occurred",
            "tenant_id",
            "app_id",
            "occurred_at",
        ),
        Index(
            "ix_grc_prompt_events_tenant_kind_occurred",
            "tenant_id",
            "event_kind",
            "occurred_at",
        ),
        {"schema": "grc"},
    )

    source: Mapped[PromptEventSource] = mapped_column(
        Enum(
            PromptEventSource,
            schema="grc",
            name="prompt_event_source",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    event_kind: Mapped[PromptEventKind] = mapped_column(
        Enum(
            PromptEventKind,
            schema="grc",
            name="prompt_event_kind",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    app_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[PromptEventAction | None] = mapped_column(
        Enum(
            PromptEventAction,
            schema="grc",
            name="prompt_event_action",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    severity: Mapped[PromptEventSeverity | None] = mapped_column(
        Enum(
            PromptEventSeverity,
            schema="grc",
            name="prompt_event_severity",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=True,
    )
    pii_categories: Mapped[dict] = mapped_column(JSONColumn, nullable=False, default=dict)
    device_fingerprint: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    user_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    vendor: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(
        Numeric(12, 6, asdecimal=False), nullable=True
    )
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.api_keys.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
