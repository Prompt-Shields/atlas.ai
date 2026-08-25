"""Agent control tower — runtime health & guardrail state (M1 of the AISPM port, #235).

Ports `lib/agent-control/*` from the ai-spm-dashboard prototype, where health,
guardrail posture, and lifecycle are derived from the in-memory discovered-agent
store. Here, `AgentControlState` is the durable, tenant-scoped record of the
*persisted overrides* an operator has applied to a `DiscoveredAgent` — pause,
quarantine, and guardrail toggling. Health itself stays derived (from
`DiscoveredAgent.last_seen_at`) rather than stored, since it's a live signal;
see `services/agent_control_service.py` for how derived + persisted state are
combined into the control-tower view.

One row per governed agent, created lazily on the first action taken against
it (see `agent_control_service.apply_action`).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GRCBase, TenantScopedMixin


class AgentLifecycleOverride(str, enum.Enum):
    """A human override on top of the discovery-derived lifecycle. `none` means no override."""

    none = "none"
    paused = "paused"
    quarantined = "quarantined"


class AgentControlAction(str, enum.Enum):
    pause = "pause"
    quarantine = "quarantine"
    toggle_guardrail = "toggle-guardrail"


class AgentControlState(GRCBase, TenantScopedMixin):
    """Persisted operator overrides for one discovered agent's control state."""

    __tablename__ = "agent_control_states"
    __table_args__ = (
        Index("ix_grc_agent_control_states_agent", "agent_id"),
        UniqueConstraint("tenant_id", "agent_id", name="uq_agent_control_state_agent"),
        {"schema": "grc"},
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.discovered_agents.id", ondelete="CASCADE"),
        nullable=False,
    )
    lifecycle_override: Mapped[AgentLifecycleOverride] = mapped_column(
        Enum(AgentLifecycleOverride, schema="grc", name="agent_lifecycle_override"),
        nullable=False,
        default=AgentLifecycleOverride.none,
    )
    guardrail_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_action: Mapped[AgentControlAction | None] = mapped_column(
        Enum(AgentControlAction, schema="grc", name="agent_control_action"),
        nullable=True,
    )
    last_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_action_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("grc.users.id", ondelete="SET NULL"),
        nullable=True,
    )
