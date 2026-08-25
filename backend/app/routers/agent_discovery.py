"""Agent discovery router — `/api/v1/agent-discovery` (M1, issue #233).

Cloud agent registry scanner: discovers AI agents across AWS Bedrock
AgentCore, Azure AI Foundry, and GCP Knowledge Catalog, and classifies each
as shadow / pending / approved / dismissed.

  GET   /agent-discovery         Viewer+   list agents, grouped by status
  POST  /agent-discovery/scan    OrgAdmin  run collectors, upsert results
  PATCH /agent-discovery/{id}    OrgAdmin  approve / dismiss

Tenant scoping is an explicit `tenant_id` filter (super-admins see all),
matching `routers/saas_vendor.py`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError, NotFoundError
from app.schemas.agent_discovery import (
    AgentDiscoveryListResponse,
    AgentDiscoveryScanResponse,
    AgentStatusUpdate,
    DiscoveredAgentResponse,
)
from app.services import agent_discovery_service

router = APIRouter(prefix="/agent-discovery", tags=["Agent Discovery"])


def _tenant_scope(user: AuthUser) -> uuid.UUID | None:
    """None for super-admins (see all tenants), else the caller's tenant id."""
    return None if user.is_super_admin() else user.tenant_id


@router.get("", response_model=AgentDiscoveryListResponse)
async def list_agents(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> AgentDiscoveryListResponse:
    """List the tenant's discovered agents, grouped by status."""
    return await agent_discovery_service.list_agents(db, _tenant_scope(user))


@router.post("/scan", response_model=AgentDiscoveryScanResponse)
async def scan_agents(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> AgentDiscoveryScanResponse:
    """Run every enabled collector; upsert by (provider, external id)."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot run a scan")
    return await agent_discovery_service.run_scan(db, user.tenant_id)


@router.patch("/{agent_id}", response_model=DiscoveredAgentResponse)
async def update_agent_status(
    agent_id: uuid.UUID,
    payload: AgentStatusUpdate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> DiscoveredAgentResponse:
    """Approve / dismiss (or reset) a discovered agent."""
    agent = await agent_discovery_service.update_status(db, _tenant_scope(user), agent_id, payload)
    if agent is None:
        raise NotFoundError("Discovered agent not found")
    return agent
