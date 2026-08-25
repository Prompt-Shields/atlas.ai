"""Device registry router — `/api/v1/devices`.

  POST /devices/register    user JWT (Bearer); mints device + token
  POST /devices/heartbeat   X-Device-Token; liveness

Registration authenticates with the user's Bearer JWT alone, which already
carries tenant_id — the macOS widget is Bearer-only (Auth0) with no tenant
X-API-Key mechanism. The minted token is the device's bound credential for all
later device-scoped calls; only its sha256 hash is stored.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    get_tenant_db_session,
    require_roles,
)
from app.auth.device_token import DeviceContext, generate_device_token, require_device_token
from app.config import get_settings
from app.database import get_db_session, set_tenant_guc
from app.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.models.device_directive import DeviceDirective
from app.models.directive_ack import DirectiveAck
from app.models.enrolled_device import EnrolledDevice
from app.models.user import Role
from app.schemas.device import DeviceHeartbeatOut, DeviceRegisterIn, DeviceRegisterOut
from app.schemas.directive import DirectiveOut, NudgeCreateIn
from app.schemas.directive_ack import DirectiveAckIn, DirectiveAckOut
from app.services.device_directive_sse import (
    device_directive_notifier,
    device_directive_stream,
    directive_available_event,
)
from app.services.directive_emit import emit_nudge_directive
from app.services.directive_status import status_for_ack

logger = logging.getLogger(__name__)

_NUDGE_TTL = timedelta(hours=24)

router = APIRouter(prefix="/devices", tags=["Devices"])


@router.post("/register", response_model=DeviceRegisterOut)
async def register_device(
    payload: DeviceRegisterIn,
    user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> DeviceRegisterOut:
    # Widget is Bearer-only (Auth0); the JWT already carries tenant_id.
    if not user.is_super_admin() and not user.tenant_id:
        raise ForbiddenError("Tenant context required")
    tenant_id = user.tenant_id
    await set_tenant_guc(db, tenant_id)

    raw_token, token_hash = generate_device_token()
    device = EnrolledDevice(
        tenant_id=tenant_id,
        user_external_id=payload.user_external_id or user.email,
        platform=payload.platform,
        app_version=payload.app_version,
        fingerprint=payload.fingerprint,
        enrollment_state="active",
        token_hash=token_hash,
        last_seen_at=datetime.now(UTC),
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return DeviceRegisterOut(device_id=str(device.id), device_token=raw_token)


@router.post("/heartbeat", response_model=DeviceHeartbeatOut)
async def heartbeat(
    device: DeviceContext = Depends(require_device_token),
    db: AsyncSession = Depends(get_db_session),
) -> DeviceHeartbeatOut:
    # RLS is active (GUC set inside require_device_token); scope by device_id.
    await db.execute(
        update(EnrolledDevice)
        .where(EnrolledDevice.id == device.device_id)
        .values(last_seen_at=datetime.now(UTC))
    )
    await db.commit()
    return DeviceHeartbeatOut(ok=True)


@router.get("/directives", response_model=list[DirectiveOut])
async def poll_directives(
    device: DeviceContext = Depends(require_device_token),  # sets tenant GUC
    db: AsyncSession = Depends(get_db_session),
) -> list[DirectiveOut]:
    now = datetime.now(UTC)
    rows = (
        (
            await db.execute(
                select(DeviceDirective)
                .where(
                    DeviceDirective.device_id == device.device_id,
                    DeviceDirective.status == "pending",
                )
                .order_by(DeviceDirective.created_at.asc())
            )
        )
        .scalars()
        .all()
    )

    fresh = []
    expired_ids = []
    for d in rows:
        exp = d.expires_at
        if exp is not None and exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)  # sqlite round-trips naive
        if exp is not None and exp <= now:
            expired_ids.append(d.id)
        else:
            fresh.append(d)

    if expired_ids:
        await db.execute(
            update(DeviceDirective)
            .where(DeviceDirective.id.in_(expired_ids))
            .values(status="expired")
            .execution_options(synchronize_session=False)
        )
    if fresh:
        await db.execute(
            update(DeviceDirective)
            .where(DeviceDirective.id.in_([d.id for d in fresh]))
            .values(status="delivered", delivered_at=now)
            .execution_options(synchronize_session=False)
        )
    await db.commit()
    return [_to_out(d) for d in fresh]


_DEVICE_SSE_TICKET_TTL = 30


@router.post("/stream/ticket")
async def create_device_stream_ticket(
    device: DeviceContext = Depends(require_device_token),
) -> dict:
    import redis.asyncio as redis

    settings = get_settings()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        ticket = secrets.token_urlsafe(32)
        await r.set(
            f"device_sse_ticket:{ticket}",
            json.dumps({"device_id": str(device.device_id)}),
            ex=_DEVICE_SSE_TICKET_TTL,
        )
    finally:
        await r.aclose()
    return {"ticket": ticket, "expires_in": _DEVICE_SSE_TICKET_TTL}


@router.get("/stream")
async def device_stream(
    ticket: str = Query(..., description="Single-use ticket from POST /devices/stream/ticket"),
) -> EventSourceResponse:
    import redis.asyncio as redis

    settings = get_settings()
    r = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        key = f"device_sse_ticket:{ticket}"
        data = await r.get(key)
        if not data:
            raise UnauthorizedError("Invalid or expired SSE ticket")
        await r.delete(key)
    finally:
        await r.aclose()
    device_id = uuid.UUID(json.loads(data)["device_id"])
    return EventSourceResponse(device_directive_stream(device_id))


@router.post(
    "/{device_id}/directives/nudge",
    response_model=DirectiveOut,
    status_code=http_status.HTTP_201_CREATED,
)
async def create_nudge_directive(
    device_id: uuid.UUID,
    payload: NudgeCreateIn,
    user: CurrentUser = Depends(require_roles(Role.SUPER_ADMIN, Role.TENANT_ADMIN)),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> DirectiveOut:
    device = (
        await db.execute(select(EnrolledDevice).where(EnrolledDevice.id == device_id))
    ).scalar_one_or_none()
    if device is None:
        raise NotFoundError("Device", str(device_id))

    directive = await emit_nudge_directive(
        db,
        tenant_id=user.tenant_id,
        device_id=device_id,
        payload=payload.model_dump(mode="json"),
        origin="admin",
        created_by=user.user_id,
        ttl=_NUDGE_TTL,
    )
    await db.commit()
    await db.refresh(directive)
    try:
        await device_directive_notifier.publish(device_id, directive_available_event())
    except Exception:
        logger.warning("directive_available publish failed", exc_info=True)
    return _to_out(directive)


@router.post("/directives/{directive_id}/ack", response_model=DirectiveAckOut)
async def ack_directive(
    directive_id: uuid.UUID,
    payload: DirectiveAckIn,
    device: DeviceContext = Depends(require_device_token),  # sets tenant GUC
    db: AsyncSession = Depends(get_db_session),
) -> DirectiveAckOut:
    directive = (
        await db.execute(
            select(DeviceDirective).where(
                DeviceDirective.id == directive_id,
                DeviceDirective.device_id == device.device_id,
            )
        )
    ).scalar_one_or_none()
    if directive is None:
        raise NotFoundError("Directive", str(directive_id))

    new_status = status_for_ack(payload.outcome)
    db.add(
        DirectiveAck(
            tenant_id=device.tenant_id,
            directive_id=directive_id,
            device_id=device.device_id,
            outcome=payload.outcome.value,
        )
    )
    directive.status = new_status
    await db.commit()
    return DirectiveAckOut(
        directive_id=str(directive_id), status=new_status, outcome=payload.outcome.value
    )


def _to_out(d: DeviceDirective) -> DirectiveOut:
    return DirectiveOut(
        id=str(d.id),
        device_id=str(d.device_id),
        kind=d.kind,
        origin=d.origin,
        status=d.status,
        payload=d.payload,
        expires_at=d.expires_at.isoformat() if d.expires_at else None,
        created_at=d.created_at.isoformat(),
    )
