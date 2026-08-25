"""Agent discovery — service layer (#233).

Query/mutation layer under `routers/agent_discovery.py`: list (grouped by
status), scan (idempotent upsert from the collectors), and status update
(approve/dismiss). All tenant-scoped via an explicit `tenant_id` filter,
matching `services/saas_vendor_service.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_discovery import AgentDiscoveryStatus, DiscoveredAgent
from app.schemas.agent_discovery import (
    AgentDiscoveryListResponse,
    AgentDiscoveryScanResponse,
    AgentStatusUpdate,
    DiscoveredAgentResponse,
)
from app.services.agent_discovery_collectors import get_collectors


def to_response(agent: DiscoveredAgent) -> DiscoveredAgentResponse:
    return DiscoveredAgentResponse(
        id=str(agent.id),
        provider=agent.provider,
        external_id=agent.external_id,
        name=agent.name,
        registry=agent.registry,
        runtime=agent.runtime,
        region=agent.region,
        description=agent.description,
        status=agent.status,
        owner_user_id=str(agent.owner_user_id) if agent.owner_user_id else None,
        last_seen_at=agent.last_seen_at,
    )


async def list_agents(db: AsyncSession, tenant_id: uuid.UUID | None) -> AgentDiscoveryListResponse:
    """All discovered agents for the tenant, grouped by status."""
    query = select(DiscoveredAgent)
    if tenant_id is not None:
        query = query.where(DiscoveredAgent.tenant_id == tenant_id)
    result = await db.execute(query.order_by(DiscoveredAgent.last_seen_at.desc()))
    grouped: AgentDiscoveryListResponse = AgentDiscoveryListResponse()
    for agent in result.scalars().all():
        getattr(grouped, agent.status.value).append(to_response(agent))
    return grouped


async def run_scan(db: AsyncSession, tenant_id: uuid.UUID) -> AgentDiscoveryScanResponse:
    """Run every enabled collector and upsert its output for this tenant.

    Idempotent by `(tenant_id, provider, external_id)`: a re-observed agent
    updates its descriptive fields + `last_seen_at` and keeps its existing
    status (a human's approve/dismiss survives re-scans); a new one is
    inserted as `shadow`.
    """
    result = await db.execute(select(DiscoveredAgent).where(DiscoveredAgent.tenant_id == tenant_id))
    existing = {(a.provider, a.external_id): a for a in result.scalars().all()}

    created = 0
    updated = 0
    touched: list[DiscoveredAgent] = []
    for collector in get_collectors():
        for collected in await collector.collect():
            key = (collector.provider, collected.external_id)
            agent = existing.get(key)
            if agent is None:
                agent = DiscoveredAgent(
                    tenant_id=tenant_id,
                    provider=collector.provider,
                    external_id=collected.external_id,
                    status=AgentDiscoveryStatus.shadow,
                )
                db.add(agent)
                existing[key] = agent
                created += 1
            else:
                updated += 1
            agent.name = collected.name
            agent.registry = collected.registry
            agent.runtime = collected.runtime
            agent.region = collected.region
            agent.description = collected.description
            agent.raw_metadata = collected.raw
            agent.last_seen_at = datetime.now(UTC)
            touched.append(agent)

    await db.commit()
    for agent in touched:
        await db.refresh(agent)
    return AgentDiscoveryScanResponse(
        scanned=len(touched),
        created=created,
        updated=updated,
        agents=[to_response(a) for a in touched],
    )


async def update_status(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    agent_id: uuid.UUID,
    payload: AgentStatusUpdate,
) -> DiscoveredAgentResponse | None:
    """Approve / dismiss (or reset) a discovered agent. None if not found."""
    query = select(DiscoveredAgent).where(DiscoveredAgent.id == agent_id)
    if tenant_id is not None:
        query = query.where(DiscoveredAgent.tenant_id == tenant_id)
    result = await db.execute(query)
    agent = result.scalar_one_or_none()
    if agent is None:
        return None
    agent.status = payload.status
    await db.commit()
    await db.refresh(agent)
    return to_response(agent)
