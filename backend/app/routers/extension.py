"""Promptly browser-extension router — `/api/v1/extension`.

API-key-authenticated public surface for the Chrome / Safari /
Firefox extensions. One endpoint in v0.1:

  POST /api/v1/extension/heartbeat
    Upsert a heartbeat row for this device. Auth: X-API-Key (tenant-
    scoped APIKey row from /api/v1/auth/api-keys).

Closes the operational loop on `endpoints.compliance.extension_
installed_count` — see `app/routers/endpoints.py` for the heuristic
this replaces. With real heartbeats we can answer:

  "How many of your N managed endpoints actually have Promptly
   installed and reporting in the past 7 days?"

instead of the IntuneDevice-fresh-within-3-days proxy.

Forward compatibility notes:
  - The endpoint accepts (but does not yet *use*) optional fields
    `prompt_volume_24h` and `top_tool_24h`. v0.2 will start
    persisting these to wire shadow-prompt analytics through to the
    drift dispatcher (per-use-case volume spikes as a new rule kind).
  - User association is best-effort. The extension may not always
    have permission to read the signed-in user email; NULL is OK.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_api_key
from app.database import get_db_session
from app.errors import UnauthorizedError
from app.models.extension_heartbeat import ExtensionDeviceHeartbeat
from app.models.user import APIKey

router = APIRouter(prefix="/extension", tags=["Extension"])


# ─── Schemas ─────────────────────────────────────────────────────────


class HeartbeatRequest(BaseModel):
    """Payload from the Promptly extension. Every field optional except
    device_fingerprint."""

    device_fingerprint: str = Field(..., min_length=8, max_length=100)
    user_email: str | None = Field(None, max_length=320)
    browser: str | None = Field(None, max_length=50)
    browser_version: str | None = Field(None, max_length=50)
    extension_version: str | None = Field(None, max_length=50)
    os_platform: str | None = Field(None, max_length=50)
    # v0.2 fields — accepted now, ignored on write. Documents the
    # intended evolution so extension authors can ship the schema
    # ahead of the backend's adoption.
    prompt_volume_24h: int | None = Field(None, ge=0)
    top_tool_24h: str | None = Field(None, max_length=100)


class HeartbeatResponse(BaseModel):
    """Returned on every heartbeat. The `next_check_in_seconds` is the
    server-suggested cadence — the extension can use this to back off
    during off-hours or speed up during incident response without an
    extension update."""

    device_fingerprint: str
    first_seen_at: datetime
    last_seen_at: datetime
    next_check_in_seconds: int


DEFAULT_NEXT_CHECK_IN_SECONDS = 300  # 5 minutes


# ─── Endpoint ────────────────────────────────────────────────────────


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def post_heartbeat(
    payload: HeartbeatRequest,
    request: Request,
    api_key_record: Annotated[APIKey, Depends(require_api_key)],
    db: AsyncSession = Depends(get_db_session),
) -> HeartbeatResponse:
    """Upsert a device heartbeat. Auth: X-API-Key.

    The api_key must be tenant-scoped — its `tenant_id` anchors the
    heartbeat row. Unscoped keys (SUPER_ADMIN) are refused here
    because there's no sensible single-tenant scope to write to.
    """
    if api_key_record.tenant_id is None:
        raise UnauthorizedError("Extension heartbeats require a tenant-scoped API key.")

    now = datetime.now(UTC)

    existing = (
        await db.execute(
            select(ExtensionDeviceHeartbeat).where(
                ExtensionDeviceHeartbeat.tenant_id == api_key_record.tenant_id,
                ExtensionDeviceHeartbeat.device_fingerprint == payload.device_fingerprint,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        record = ExtensionDeviceHeartbeat(
            tenant_id=api_key_record.tenant_id,
            device_fingerprint=payload.device_fingerprint,
            user_email=(payload.user_email or "").strip().lower() or None,
            browser=payload.browser,
            browser_version=payload.browser_version,
            extension_version=payload.extension_version,
            os_platform=payload.os_platform,
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(record)
    else:
        # Upsert: bump last_seen + refresh metadata. first_seen_at
        # is preserved across the row's lifetime.
        existing.last_seen_at = now
        if payload.user_email:
            existing.user_email = payload.user_email.strip().lower() or None
        # Only overwrite metadata fields when the heartbeat actually
        # carries them — avoids erasing known-good values when a
        # later heartbeat omits a field (e.g. browser version not
        # detected on that page).
        if payload.browser is not None:
            existing.browser = payload.browser
        if payload.browser_version is not None:
            existing.browser_version = payload.browser_version
        if payload.extension_version is not None:
            existing.extension_version = payload.extension_version
        if payload.os_platform is not None:
            existing.os_platform = payload.os_platform
        record = existing

    await db.commit()
    await db.refresh(record)

    return HeartbeatResponse(
        device_fingerprint=record.device_fingerprint,
        first_seen_at=record.first_seen_at,
        last_seen_at=record.last_seen_at,
        next_check_in_seconds=DEFAULT_NEXT_CHECK_IN_SECONDS,
    )


# ─── Helpers for other services (compliance aggregate) ───────────────


# How long after `last_seen_at` we still count a device as
# "extension installed". Default 7 days mirrors typical desktop
# work-week cadence — devices off for a long weekend still count.
EXTENSION_FRESH_WINDOW = timedelta(days=7)


def fresh_cutoff(now: datetime | None = None) -> datetime:
    """The cutoff timestamp for the freshness filter."""
    return (now or datetime.now(UTC)) - EXTENSION_FRESH_WINDOW


# ─── Admin observability: list this tenant's heartbeats ──────────────


class HeartbeatRow(BaseModel):
    device_fingerprint: str
    user_email: str | None
    browser: str | None
    browser_version: str | None
    extension_version: str | None
    os_platform: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    is_fresh: bool


class HeartbeatListResponse(BaseModel):
    devices: list[HeartbeatRow]
    total: int
    fresh_count: int


@router.get("/devices", response_model=HeartbeatListResponse)
async def list_devices(
    request: Request,
    api_key_record: Annotated[APIKey, Depends(require_api_key)],
    db: AsyncSession = Depends(get_db_session),
) -> HeartbeatListResponse:
    """List all extension heartbeats for the caller's tenant.

    Auth: the same X-API-Key the extension uses. Useful for the
    atlas dashboard's eventual "X of Y devices reporting" view in
    v0.2 — the endpoint is API-key-gated rather than JWT-gated so
    automation can poll it without needing a logged-in admin.
    """
    if api_key_record.tenant_id is None:
        raise UnauthorizedError("Listing heartbeats requires a tenant-scoped API key.")

    cutoff = fresh_cutoff()
    rows = (
        (
            await db.execute(
                select(ExtensionDeviceHeartbeat)
                .where(ExtensionDeviceHeartbeat.tenant_id == api_key_record.tenant_id)
                .order_by(ExtensionDeviceHeartbeat.last_seen_at.desc())
            )
        )
        .scalars()
        .all()
    )

    serialised: list[HeartbeatRow] = []
    fresh = 0
    for row in rows:
        last_seen = (
            row.last_seen_at
            if row.last_seen_at.tzinfo is not None
            else row.last_seen_at.replace(tzinfo=UTC)
        )
        is_fresh = last_seen >= cutoff
        if is_fresh:
            fresh += 1
        serialised.append(
            HeartbeatRow(
                device_fingerprint=row.device_fingerprint,
                user_email=row.user_email,
                browser=row.browser,
                browser_version=row.browser_version,
                extension_version=row.extension_version,
                os_platform=row.os_platform,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
                is_fresh=is_fresh,
            )
        )

    return HeartbeatListResponse(
        devices=serialised,
        total=len(serialised),
        fresh_count=fresh,
    )
