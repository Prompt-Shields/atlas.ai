"""Agent control tower — service layer (#235).

Combines `DiscoveredAgent` (identity + discovery status, from #233) with the
persisted overrides in `AgentControlState` into the control-tower view:
health is derived live from `last_seen_at` recency, lifecycle is the
discovery status unless a pause/quarantine override is set, and guardrail
posture is the persisted `guardrail_enabled` flag (enabled by default).

Actions are idempotent toggles: `pause`/`quarantine` flip their override on
or off, `toggle-guardrail` flips `guardrail_enabled`. Every action writes an
audit-log entry via `services/audit.log_audit_event`, matching
`routers/users.py`'s `role.changed` convention.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_control import AgentControlAction, AgentControlState, AgentLifecycleOverride
from app.models.agent_discovery import AgentDiscoveryStatus, DiscoveredAgent
from app.schemas.agent_control import (
    AgentControlListResponse,
    AgentControlStateResponse,
    AgentHealth,
    AgentLifecycle,
)
from app.services.audit import log_audit_event

_HEALTHY_WITHIN = 60 * 60  # seconds — seen within the last hour
_DEGRADED_WITHIN = 24 * 60 * 60  # seconds — seen within the last day

_LIFECYCLE_FOR_STATUS = {
    AgentDiscoveryStatus.shadow: AgentLifecycle.unmonitored,
    AgentDiscoveryStatus.pending: AgentLifecycle.provisioning,
    AgentDiscoveryStatus.approved: AgentLifecycle.active,
    AgentDiscoveryStatus.dismissed: AgentLifecycle.unmonitored,
}

_LIFECYCLE_FOR_OVERRIDE = {
    AgentLifecycleOverride.paused: AgentLifecycle.paused,
    AgentLifecycleOverride.quarantined: AgentLifecycle.quarantined,
}


def _derive_health(agent: DiscoveredAgent, *, now: datetime) -> AgentHealth:
    last_seen_at = agent.last_seen_at
    if last_seen_at.tzinfo is None:
        # SQLite drops tzinfo on read even for DateTime(timezone=True) columns.
        last_seen_at = last_seen_at.replace(tzinfo=UTC)
    age = (now - last_seen_at).total_seconds()
    if age <= _HEALTHY_WITHIN:
        return AgentHealth.healthy
    if age <= _DEGRADED_WITHIN:
        return AgentHealth.degraded
    return AgentHealth.unhealthy


def _derive_lifecycle(agent: DiscoveredAgent, state: AgentControlState | None) -> AgentLifecycle:
    if state is not None:
        overridden = _LIFECYCLE_FOR_OVERRIDE.get(state.lifecycle_override)
        if overridden is not None:
            return overridden
    return _LIFECYCLE_FOR_STATUS[agent.status]


def _to_response(
    agent: DiscoveredAgent, state: AgentControlState | None, *, now: datetime
) -> AgentControlStateResponse:
    return AgentControlStateResponse(
        id=str(agent.id),
        provider=agent.provider,
        name=agent.name,
        registry=agent.registry,
        discovery_status=agent.status,
        health=_derive_health(agent, now=now),
        lifecycle=_derive_lifecycle(agent, state),
        guardrail_enabled=state.guardrail_enabled if state is not None else True,
        last_action=state.last_action if state is not None else None,
        last_action_at=state.last_action_at if state is not None else None,
        last_seen_at=agent.last_seen_at,
    )


async def list_control_states(
    db: AsyncSession, tenant_id: uuid.UUID | None
) -> AgentControlListResponse:
    """Every governed (non-dismissed) discovered agent, control-tower enriched."""
    query = select(DiscoveredAgent).where(DiscoveredAgent.status != AgentDiscoveryStatus.dismissed)
    if tenant_id is not None:
        query = query.where(DiscoveredAgent.tenant_id == tenant_id)
    agents = (await db.execute(query.order_by(DiscoveredAgent.last_seen_at.desc()))).scalars().all()
    if not agents:
        return AgentControlListResponse()

    states_query = select(AgentControlState).where(
        AgentControlState.agent_id.in_([a.id for a in agents])
    )
    states_by_agent = {s.agent_id: s for s in (await db.execute(states_query)).scalars().all()}

    now = datetime.now(UTC)
    return AgentControlListResponse(
        agents=[_to_response(agent, states_by_agent.get(agent.id), now=now) for agent in agents]
    )


async def _get_or_create_state(
    db: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID
) -> AgentControlState:
    result = await db.execute(
        select(AgentControlState).where(
            AgentControlState.tenant_id == tenant_id,
            AgentControlState.agent_id == agent_id,
        )
    )
    state = result.scalar_one_or_none()
    if state is None:
        # Explicit defaults: the mapped_column `default=` only lands on flush,
        # and callers read these fields before committing (e.g. audit details).
        state = AgentControlState(
            tenant_id=tenant_id,
            agent_id=agent_id,
            lifecycle_override=AgentLifecycleOverride.none,
            guardrail_enabled=True,
        )
        db.add(state)
    return state


async def apply_action(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    agent_id: uuid.UUID,
    action: AgentControlAction,
    actor_id: uuid.UUID | None,
) -> AgentControlStateResponse | None:
    """Apply pause/quarantine/toggle-guardrail. None if the agent isn't found for this tenant."""
    result = await db.execute(
        select(DiscoveredAgent).where(
            DiscoveredAgent.id == agent_id, DiscoveredAgent.tenant_id == tenant_id
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        return None

    state = await _get_or_create_state(db, tenant_id, agent_id)

    if action == AgentControlAction.pause:
        state.lifecycle_override = (
            AgentLifecycleOverride.none
            if state.lifecycle_override == AgentLifecycleOverride.paused
            else AgentLifecycleOverride.paused
        )
    elif action == AgentControlAction.quarantine:
        state.lifecycle_override = (
            AgentLifecycleOverride.none
            if state.lifecycle_override == AgentLifecycleOverride.quarantined
            else AgentLifecycleOverride.quarantined
        )
    elif action == AgentControlAction.toggle_guardrail:
        state.guardrail_enabled = not state.guardrail_enabled

    now = datetime.now(UTC)
    state.last_action = action
    state.last_action_at = now
    state.last_action_by = actor_id

    await log_audit_event(
        db,
        event_type="agent_control.action",
        action=action.value,
        actor_id=actor_id,
        tenant_id=tenant_id,
        resource_type="discovered_agent",
        resource_id=str(agent_id),
        details={
            "lifecycle_override": state.lifecycle_override.value,
            "guardrail_enabled": state.guardrail_enabled,
        },
    )

    await db.commit()
    await db.refresh(agent)
    await db.refresh(state)
    return _to_response(agent, state, now=now)
