"""MCP server discovery router — `/api/v1/discover/mcp` (M3, issue #241).

Collector → correlation → scoring pipeline for MCP servers observed across
IDE configs, network traffic, browser telemetry, and host process lists.

  GET   /discover/mcp              Viewer+   list scored candidates
  POST  /discover/mcp/scan         OrgAdmin  run collectors, correlate, score, upsert
  POST  /discover/mcp/{id}/promote OrgAdmin  create an AI-inventory asset for a candidate
  PATCH /discover/mcp/{id}         OrgAdmin  dismiss (or reset) a candidate

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
from app.schemas.mcp_discovery import (
    DiscoveredMcpServerResponse,
    McpDiscoveryListResponse,
    McpDiscoveryScanResponse,
    McpServerPromoteResponse,
    McpServerStatusUpdate,
)
from app.services import mcp_discovery_service

router = APIRouter(prefix="/discover/mcp", tags=["MCP Discovery"])


def _tenant_scope(user: AuthUser) -> uuid.UUID | None:
    """None for super-admins (see all tenants), else the caller's tenant id."""
    return None if user.is_super_admin() else user.tenant_id


@router.get("", response_model=McpDiscoveryListResponse)
async def list_mcp_servers(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> McpDiscoveryListResponse:
    """List the tenant's discovered MCP servers, highest-confidence first."""
    return await mcp_discovery_service.list_servers(db, _tenant_scope(user))


@router.post("/scan", response_model=McpDiscoveryScanResponse)
async def scan_mcp_servers(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> McpDiscoveryScanResponse:
    """Run every collector; correlate + score; upsert by canonical name+host."""
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant — cannot run a scan")
    return await mcp_discovery_service.run_scan(db, user.tenant_id)


@router.post("/{server_id}/promote", response_model=McpServerPromoteResponse)
async def promote_mcp_server(
    server_id: uuid.UUID,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> McpServerPromoteResponse:
    """Create an AI-inventory asset for a discovered MCP server."""
    if user.tenant_id is None or user.org_id is None:
        raise ForbiddenError("User has no tenant/org — cannot promote")
    result = await mcp_discovery_service.promote(db, user.tenant_id, user.org_id, server_id)
    if result is None:
        raise NotFoundError("Discovered MCP server not found")
    return result


@router.patch("/{server_id}", response_model=DiscoveredMcpServerResponse)
async def update_mcp_server_status(
    server_id: uuid.UUID,
    payload: McpServerStatusUpdate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> DiscoveredMcpServerResponse:
    """Dismiss (or reset) a discovered MCP server."""
    server = await mcp_discovery_service.update_status(db, _tenant_scope(user), server_id, payload)
    if server is None:
        raise NotFoundError("Discovered MCP server not found")
    return server
