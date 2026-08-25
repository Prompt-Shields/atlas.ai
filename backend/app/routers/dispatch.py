"""Dispatch and SSE router — real-time event streaming."""

from __future__ import annotations

import json
import secrets
import uuid as _uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import AuthUser, get_tenant_db_session
from app.errors import ForbiddenError, UnauthorizedError
from app.models.dispatch import DispatchEvent
from app.schemas.dispatch import DispatchEventListResponse, DispatchEventResponse
from app.services.dispatch_service import stream_events


def _safe_json_loads(raw: str | None, fallback: Any = None) -> Any:
    if fallback is None:
        fallback = {}
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return fallback


router = APIRouter(prefix="/dispatch", tags=["Dispatch"])

SSE_TICKET_TTL_SECONDS = 30


@router.get("/events", response_model=DispatchEventListResponse)
async def list_dispatch_events(
    user: AuthUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> DispatchEventListResponse:
    """List dispatch events (tenant-scoped)."""
    query = select(DispatchEvent)
    count_query = select(func.count(DispatchEvent.id))

    if not user.is_super_admin():
        query = query.where(DispatchEvent.tenant_id == user.tenant_id)
        count_query = count_query.where(DispatchEvent.tenant_id == user.tenant_id)

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    result = await db.execute(
        query.order_by(DispatchEvent.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    events = list(result.scalars().all())

    return DispatchEventListResponse(
        events=[
            DispatchEventResponse(
                id=str(e.id),
                tenant_id=str(e.tenant_id),
                org_id=str(e.org_id),
                correlation_id=str(e.correlation_id),
                event_type=e.event_type,
                payload=_safe_json_loads(e.payload),
                severity=e.severity,
                delivered=e.delivered,
                delivered_at=e.delivered_at,
                created_at=e.created_at,
                is_test_data=e.is_test_data,
            )
            for e in events
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/stream/ticket")
async def create_sse_ticket(
    user: AuthUser,
) -> dict:
    """Create a short-lived, single-use opaque ticket for SSE connection.

    The EventSource browser API cannot send custom headers, so instead of
    passing the JWT in a query parameter (which leaks to logs, history, and
    Referer headers), the client first obtains this ticket via an
    authenticated request, then connects using the opaque ticket.

    Flow:
        1. POST /dispatch/stream/ticket  (Authorization: Bearer <jwt>)
        2. GET  /dispatch/stream?ticket=<opaque>
    """
    if not user.tenant_id and not user.is_super_admin():
        raise ForbiddenError("Tenant context required for event streaming")
    if not user.tenant_id:
        raise ForbiddenError("Super admin must specify tenant context for streaming")

    import redis.asyncio as redis

    from app.config import get_settings

    settings = get_settings()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        ticket = secrets.token_urlsafe(32)
        ticket_key = f"sse_ticket:{ticket}"
        ticket_data = json.dumps({"tenant_id": str(user.tenant_id), "user_id": str(user.user_id)})
        await r.set(ticket_key, ticket_data, ex=SSE_TICKET_TTL_SECONDS)
    finally:
        await r.aclose()

    return {"ticket": ticket, "expires_in": SSE_TICKET_TTL_SECONDS}


@router.get("/stream")
async def sse_stream(
    request: Request,
    ticket: str = Query(..., description="Single-use opaque ticket from POST /stream/ticket"),
) -> EventSourceResponse:
    """SSE stream for real-time dispatch events (tenant-scoped).

    Connect via:
        1. const { ticket } = await fetch('/api/v1/dispatch/stream/ticket', { method: 'POST', headers: { Authorization: ... } }).then(r => r.json());
        2. const es = new EventSource('/api/v1/dispatch/stream?ticket=' + ticket);
        es.addEventListener('correlation_created', (e) => { ... });
    """
    from app.auth.rate_limit import check_rate_limit

    client_ip = request.client.host if request.client else "unknown"
    await check_rate_limit(f"sse_connect:{client_ip}", max_attempts=10, window_seconds=60)

    import redis.asyncio as redis

    from app.config import get_settings

    settings = get_settings()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        ticket_key = f"sse_ticket:{ticket}"
        ticket_data = await r.get(ticket_key)
        if not ticket_data:
            raise UnauthorizedError("Invalid or expired SSE ticket")
        # Single-use: delete immediately after consumption
        await r.delete(ticket_key)
    finally:
        await r.aclose()

    data = json.loads(ticket_data)
    tenant_id = _uuid.UUID(data["tenant_id"])

    return EventSourceResponse(stream_events(tenant_id))
