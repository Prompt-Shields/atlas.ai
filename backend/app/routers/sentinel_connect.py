"""Microsoft Sentinel connect, seeded preview, and live forwarder.

Two modes share one integration row:

  * **Preview** — an org admin submits a workspace label / table / event-type
    mapping and `/events` serves a seeded stream shaped like the
    `PromptShieldsActivity_CL` custom table. No Azure call. This is what
    ships when the customer's Sentinel admin has not yet run the Bicep
    template.
  * **Live forwarding** — the same connect call also carries the Azure Monitor
    coordinates (tenant id, app registration, DCE URI, DCR immutable id).
    Once stored, `app.services.sentinel_forwarder` ships real prompt telemetry
    into the customer's workspace and `/events` reports
    `forwarder_configured=true`. See
    docs/integrations/microsoft-sentinel/spec.md §7.

Registered before the generic `integrations` router in main.py, same
as mdm_connect.router, so its literal /integrations/sentinel/* paths
resolve ahead of any generic /integrations/{provider} pattern.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin, get_tenant_db_session
from app.database import get_db_session
from app.errors import AppException, ForbiddenError, NotFoundError
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.sentinel_forward import (
    SentinelDeadLetter,
    SentinelDeadLetterStatus,
    SentinelForwardCursor,
)
from app.schemas.integration import IntegrationCard
from app.schemas.sentinel import (
    SentinelConnectRequest,
    SentinelDeadLetterListResponse,
    SentinelDeadLetterOut,
    SentinelEvent,
    SentinelEventStreamResponse,
    SentinelForwarderStatus,
    SentinelForwardRunResponse,
    SentinelReplayResponse,
)
from app.services import sentinel_forwarder, sentinel_service
from app.services.crypto import encrypt_token
from app.services.integration_registry import get_provider

logger = structlog.get_logger()
router = APIRouter(prefix="/integrations/sentinel", tags=["Integrations"])

# Azure Monitor is not a fast endpoint under load; the forwarder's own retry
# budget assumes a request either completes or fails within this window.
_HTTP_TIMEOUT = httpx.Timeout(30.0)


async def _get_integration(db: AsyncSession, tenant_id: uuid.UUID) -> Integration | None:
    return (
        await db.execute(
            select(Integration).where(
                Integration.tenant_id == tenant_id,
                Integration.provider == IntegrationProvider.SENTINEL,
            )
        )
    ).scalar_one_or_none()


def _to_card(integration: Integration) -> IntegrationCard:
    meta = get_provider(integration.provider)
    try:
        config = json.loads(integration.config_json) if integration.config_json else {}
    except json.JSONDecodeError:
        config = {}
    return IntegrationCard(
        meta={
            "provider": meta.provider,
            "display_name": meta.display_name,
            "short_name": meta.short_name,
            "category": meta.category,
            "vendor": meta.vendor,
            "logo_slug": meta.logo_slug,
            "description": meta.description,
            "capabilities": meta.capabilities,
            "available": meta.available,
            "onboarding_recommended": meta.onboarding_recommended,
        },
        status=integration.status,
        integration_id=str(integration.id),
        display_name=integration.display_name,
        external_id=integration.external_id,
        external_name=integration.external_name,
        scopes=[],
        last_synced_at=integration.last_synced_at,
        last_error=integration.last_error,
        connected_at=integration.connected_at,
        config=config,
    )


@router.post("/connect", response_model=IntegrationCard)
async def sentinel_connect(
    payload: SentinelConnectRequest,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> IntegrationCard:
    """Store the workspace/table mapping and, when supplied, the Azure config.

    The client secret is Fernet-encrypted into `refresh_token_encrypted` (the
    long-lived credential slot) and never appears in a response. Supplying new
    Azure coordinates invalidates any cached bearer token, so the next forward
    re-authenticates rather than replaying a token minted for the old app.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    config: dict[str, object] = {
        "workspace_name": payload.workspace_name,
        "table_name": payload.table_name,
        "enabled_event_types": [t.value for t in payload.enabled_event_types],
    }
    forwarder_configured = bool(payload.client_secret)
    if forwarder_configured:
        config.update(
            {
                "azure_tenant_id": payload.azure_tenant_id,
                "client_id": payload.client_id,
                "dce_url": payload.dce_url,
                "dcr_immutable_id": payload.dcr_immutable_id,
                "stream_name": payload.stream_name,
            }
        )

    config_json = json.dumps(config)
    now = datetime.now(UTC)
    record = await _get_integration(db, user.tenant_id)
    if record is None:
        record = Integration(
            tenant_id=user.tenant_id,
            provider=IntegrationProvider.SENTINEL,
            display_name=f"Microsoft Sentinel ({payload.workspace_name})",
            external_id=payload.workspace_name,
            external_name=payload.workspace_name,
            config_json=config_json,
            status=IntegrationStatus.CONNECTED,
            is_active=True,
            connected_by_user_id=user.user_id,
            connected_at=now,
        )
        db.add(record)
    else:
        record.display_name = f"Microsoft Sentinel ({payload.workspace_name})"
        record.external_id = payload.workspace_name
        record.external_name = payload.workspace_name
        record.config_json = config_json
        record.status = IntegrationStatus.CONNECTED
        record.is_active = True
        record.connected_by_user_id = user.user_id
        record.connected_at = now
        record.last_error = None

    if forwarder_configured:
        record.refresh_token_encrypted = encrypt_token(payload.client_secret or "")
        record.access_token_encrypted = None
        record.access_token_expires_at = None

    await db.commit()
    await db.refresh(record)
    logger.info(
        "sentinel_connected",
        tenant_id=str(user.tenant_id),
        integration_id=str(record.id),
    )
    return _to_card(record)


@router.get("/events", response_model=SentinelEventStreamResponse)
async def sentinel_events(
    user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> SentinelEventStreamResponse:
    """Seeded AI-relevant event stream, filtered to the tenant's mapping.

    Returns `connected=False` with no events until the tenant has
    connected Sentinel via `/connect`.
    """
    if user.tenant_id is None:
        return SentinelEventStreamResponse(connected=False)

    record = await _get_integration(db, user.tenant_id)
    if record is None or record.status != IntegrationStatus.CONNECTED:
        return SentinelEventStreamResponse(connected=False)

    try:
        config = json.loads(record.config_json) if record.config_json else {}
    except json.JSONDecodeError:
        config = {}

    enabled_types = [
        sentinel_service.SentinelEventType(t) for t in config.get("enabled_event_types", [])
    ]
    raw_events = sentinel_service.generate_events(enabled_event_types=enabled_types)
    return SentinelEventStreamResponse(
        connected=True,
        workspace_name=config.get("workspace_name"),
        table_name=config.get("table_name"),
        enabled_event_types=enabled_types,
        events=[SentinelEvent(**e) for e in raw_events],
        forwarder_configured=sentinel_forwarder.load_config(record) is not None,
    )


# ─── Forwarder ───────────────────────────────────────────────────────


async def _require_integration(db: AsyncSession, tenant_id: uuid.UUID) -> Integration:
    record = await _get_integration(db, tenant_id)
    if record is None:
        raise NotFoundError("Sentinel is not connected for this tenant")
    return record


@router.get("/status", response_model=SentinelForwarderStatus)
async def sentinel_forwarder_status(
    user: AuthUser,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> SentinelForwarderStatus:
    """Delivery counters, last run, and the dead-letter backlog.

    The backlog is the number that matters: the spec's audit guarantee is only
    meaningful if undelivered batches are visible to the admin who can act.
    """
    if user.tenant_id is None:
        return SentinelForwarderStatus(connected=False, forwarder_configured=False)

    record = await _get_integration(db, user.tenant_id)
    if record is None:
        return SentinelForwarderStatus(connected=False, forwarder_configured=False)

    config = sentinel_forwarder.load_config(record)

    try:
        raw = json.loads(record.config_json) if record.config_json else {}
    except json.JSONDecodeError:
        raw = {}
    enabled_types = [
        sentinel_service.SentinelEventType(t) for t in raw.get("enabled_event_types", [])
    ]

    cursor = (
        await db.execute(
            select(SentinelForwardCursor).where(
                SentinelForwardCursor.tenant_id == user.tenant_id,
                SentinelForwardCursor.integration_id == record.id,
            )
        )
    ).scalar_one_or_none()

    pending = (
        await db.execute(
            select(func.count())
            .select_from(SentinelDeadLetter)
            .where(
                SentinelDeadLetter.tenant_id == user.tenant_id,
                SentinelDeadLetter.integration_id == record.id,
                SentinelDeadLetter.status == SentinelDeadLetterStatus.PENDING,
            )
        )
    ).scalar_one()

    return SentinelForwarderStatus(
        connected=record.status == IntegrationStatus.CONNECTED,
        forwarder_configured=config is not None,
        workspace_name=raw.get("workspace_name"),
        table_name=raw.get("table_name"),
        stream_name=config.stream_name if config else None,
        dcr_immutable_id=config.dcr_immutable_id if config else None,
        enabled_event_types=enabled_types,
        events_forwarded=cursor.events_forwarded if cursor else 0,
        events_skipped=cursor.events_skipped if cursor else 0,
        batches_sent=cursor.batches_sent if cursor else 0,
        batches_dead_lettered=cursor.batches_dead_lettered if cursor else 0,
        pending_dead_letters=pending,
        last_run_at=cursor.last_run_at if cursor else None,
        last_success_at=cursor.last_success_at if cursor else None,
        last_error=cursor.last_error if cursor else None,
    )


@router.post("/forward", response_model=SentinelForwardRunResponse)
async def sentinel_forward_now(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> SentinelForwardRunResponse:
    """Run the forwarder once for this tenant ("Forward now").

    The worker runs this on a loop; this endpoint exists so an admin can prove
    the pipe works during setup without waiting for the next cycle.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    record = await _require_integration(db, user.tenant_id)
    if sentinel_forwarder.load_config(record) is None:
        raise AppException(
            code="SENTINEL_NOT_CONFIGURED",
            message=(
                "Sentinel is connected in preview mode. Add the Azure Monitor "
                "coordinates (tenant id, client id/secret, DCE URI, DCR immutable "
                "id) to start forwarding."
            ),
            status_code=409,
        )

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        result = await sentinel_forwarder.forward_integration(db, record, client=client)

    return SentinelForwardRunResponse(
        events_read=result.events_read,
        events_forwarded=result.events_forwarded,
        events_skipped=result.events_skipped,
        batches_sent=result.batches_sent,
        batches_dead_lettered=result.batches_dead_lettered,
        error=result.error,
    )


@router.get("/dead-letters", response_model=SentinelDeadLetterListResponse)
async def sentinel_dead_letters(
    user: AuthUser,
    status: SentinelDeadLetterStatus | None = Query(
        default=None, description="Filter by status; omit for all"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> SentinelDeadLetterListResponse:
    """Undelivered batches, newest first. Payloads are excluded."""
    if user.tenant_id is None:
        return SentinelDeadLetterListResponse()

    filters = [SentinelDeadLetter.tenant_id == user.tenant_id]
    if status is not None:
        filters.append(SentinelDeadLetter.status == status)

    total = (
        await db.execute(select(func.count()).select_from(SentinelDeadLetter).where(*filters))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                select(SentinelDeadLetter)
                .where(*filters)
                .order_by(SentinelDeadLetter.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return SentinelDeadLetterListResponse(
        items=[SentinelDeadLetterOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
    )


@router.post("/dead-letters/{dead_letter_id}/replay", response_model=SentinelReplayResponse)
async def sentinel_replay_dead_letter(
    dead_letter_id: uuid.UUID,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> SentinelReplayResponse:
    """Re-send one dead-lettered batch.

    Already-replayed batches are not re-sent — a second delivery would double
    the rows in the customer's table for no gain.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    dead_letter = (
        await db.execute(
            select(SentinelDeadLetter).where(
                SentinelDeadLetter.id == dead_letter_id,
                SentinelDeadLetter.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if dead_letter is None:
        raise NotFoundError("Dead letter not found")

    if dead_letter.status == SentinelDeadLetterStatus.REPLAYED:
        return SentinelReplayResponse(
            replayed=False,
            status=dead_letter.status,
            detail="Already replayed",
        )

    record = await _require_integration(db, user.tenant_id)
    if sentinel_forwarder.load_config(record) is None:
        raise AppException(
            code="SENTINEL_NOT_CONFIGURED",
            message="Sentinel has no forwarder config to replay against",
            status_code=409,
        )

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
        ok = await sentinel_forwarder.replay_dead_letter(db, dead_letter, record, client=client)

    return SentinelReplayResponse(
        replayed=ok,
        status=dead_letter.status,
        detail=None if ok else dead_letter.error_detail,
    )
