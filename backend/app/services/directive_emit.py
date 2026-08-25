"""Shared directive emission + cooldown helpers. Imported by BOTH the devices
router (admin producer) and the risk engine (worker producer). Lives in services
(not the router) so the worker-run risk engine doesn't pull the FastAPI/router/
auth graph at import time."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.device_directive import DeviceDirective

DEFAULT_NUDGE_TTL = timedelta(hours=24)


async def emit_nudge_directive(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    payload: dict,
    origin: str,
    created_by: uuid.UUID | None = None,
    ttl: timedelta = DEFAULT_NUDGE_TTL,
) -> DeviceDirective:
    """Create a pending nudge directive (caller commits). Shared by admin
    (origin='admin') and risk engine (origin='risk_engine')."""
    directive = DeviceDirective(
        tenant_id=tenant_id,
        device_id=device_id,
        kind="nudge",
        origin=origin,
        status="pending",
        payload=payload,
        created_by=created_by,
        expires_at=datetime.now(UTC) + ttl,
    )
    db.add(directive)
    return directive


async def recent_directive_exists(
    db: AsyncSession,
    *,
    device_id: uuid.UUID,
    origin: str,
    within: timedelta,
) -> bool:
    """True if a directive of this origin was created for the device within the
    window — the risk engine's cooldown so a repeat scan doesn't re-nudge."""
    since = datetime.now(UTC) - within
    row = (
        await db.execute(
            select(DeviceDirective.id)
            .where(
                DeviceDirective.device_id == device_id,
                DeviceDirective.origin == origin,
                DeviceDirective.created_at >= since,
            )
            .limit(1)
        )
    ).first()
    return row is not None
