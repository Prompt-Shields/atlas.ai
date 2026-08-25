"""Agent control tower router — `/api/v1/agent-control` (M1, issue #235).

Runtime health & guardrail state for governed agents: health and lifecycle
are derived from `discovered_agents` (#233) at read time, persisted
overrides (pause/quarantine/guardrail toggle) live in `agent_control_states`.

  GET  /agent-control              Viewer+   control-tower view, all governed agents
  POST /agent-control/{id}/action  OrgAdmin  pause | quarantine | toggle-guardrail

Tenant scoping is an explicit `tenant_id` filter (super-admins see all),
matching `routers/agent_discovery.py`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError, NotFoundError
from app.schemas.agent_control import (
    AgentControlActionRequest,
    AgentControlListResponse,
    AgentControlStateResponse,
)
from app.services import agent_control_service

router = APIRouter(prefix="/agent-control", tags=["Agent Control"])


def _tenant_scope(user: AuthUser) -> uuid.UUID | None:
    """None for super-admins (see all tenants), else the caller's tenant id."""
    return None if user.is_super_admin() else user.tenant_id


@router.get("", response_model=AgentControlListResponse)
async def list_control_states(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> AgentControlListResponse:
    """List the tenant's governed agents with derived health/lifecycle + guardrail state."""
    return await agent_control_service.list_control_states(db, _tenant_scope(user))


@router.post("/{agent_id}/action", response_model=AgentControlStateResponse)
async def apply_action(
    agent_id: uuid.UUID,
    payload: AgentControlActionRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> AgentControlStateResponse:
    """Pause / quarantine / toggle-guardrail on a governed agent."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot control an agent")
    state = await agent_control_service.apply_action(
        db, user.tenant_id, agent_id, payload.action, user.user_id
    )
    if state is None:
        raise NotFoundError("Discovered agent not found")
    return state
