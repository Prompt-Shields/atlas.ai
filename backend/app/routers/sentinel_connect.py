"""Microsoft Sentinel connect + seeded event stream (issue #247).

v1 scope: register Sentinel in the integrations catalog, let an org
admin submit a workspace/table + which Prompt Shields event types map
into it, and surface a seeded event stream shaped like the
`PromptShieldsActivity_CL` custom table. No live call to Azure Monitor's
Logs Ingestion API — see
docs/feedbacks/integrations/microsoft-sentinel/spec.md for the real
ingestion design (forwarder + DCR/DCE), which is out of scope here.

Registered before the generic `integrations` router in main.py, same
as mdm_connect.router, so its literal /integrations/sentinel/* paths
resolve ahead of any generic /integrations/{provider} pattern.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, OrgAdmin
from app.database import get_db_session
from app.errors import ForbiddenError
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.schemas.integration import IntegrationCard
from app.schemas.sentinel import (
    SentinelConnectRequest,
    SentinelEvent,
    SentinelEventStreamResponse,
)
from app.services import sentinel_service
from app.services.integration_registry import get_provider

logger = structlog.get_logger()
router = APIRouter(prefix="/integrations/sentinel", tags=["Integrations"])


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
    """Store the Sentinel workspace/table + event-type mapping.

    v1 makes no live call to Azure Monitor — see module docstring.
    """
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant")

    config_json = json.dumps(
        {
            "workspace_name": payload.workspace_name,
            "table_name": payload.table_name,
            "enabled_event_types": [t.value for t in payload.enabled_event_types],
        }
    )
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
    )
