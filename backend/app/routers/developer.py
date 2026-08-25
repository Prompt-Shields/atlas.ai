"""Developer API enablement — routes for managing API keys, event types,
and ingesting/reviewing events.

All routes mounted under /api/v1/developer.

Authentication:
  - Management routes (keys, event types) require JWT (dashboard users).
  - Ingestion routes (POST /events) accept JWT OR X-API-Key with the
    `events:write` scope.
  - Listing routes (GET /events) accept JWT OR X-API-Key with `events:read`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.api_keys import generate_api_key
from app.auth.dependencies import AuthUser
from app.auth.developer_scope import (
    EventReader,
    EventTypeReader,
    EventTypeWriter,
    EventWriter,
)
from app.database import get_db_session
from app.errors import ForbiddenError, NotFoundError
from app.models.developer import (
    DeveloperAPIKeyScope,
    DeveloperEvent,
    DeveloperEventDefinition,
    DeveloperScope,
    EventSeverity,
)
from app.models.user import APIKey
from app.schemas.developer import (
    DeveloperAPIKeyCreate,
    DeveloperAPIKeyCreateResponse,
    DeveloperAPIKeyListResponse,
    DeveloperAPIKeyResponse,
    DeveloperAPIKeyScopesUpdate,
    EventDefinitionCreate,
    EventDefinitionListResponse,
    EventDefinitionResponse,
    EventDefinitionUpdate,
    EventIngestRequest,
    EventIngestResponse,
    EventListResponse,
    EventResponse,
)

router = APIRouter(prefix="/developer", tags=["Developer API"])


# ──────────────────────────────────────────────────────────────────────
# API Key management (JWT-only — dashboard UI)
# ──────────────────────────────────────────────────────────────────────


def _scopes_to_response(scopes: list[DeveloperAPIKeyScope]) -> list[DeveloperScope]:
    return [s.scope for s in scopes]


async def _load_key_scopes(db: AsyncSession, api_key_id: uuid.UUID) -> list[DeveloperAPIKeyScope]:
    stmt = select(DeveloperAPIKeyScope).where(DeveloperAPIKeyScope.api_key_id == api_key_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.post(
    "/keys",
    response_model=DeveloperAPIKeyCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new developer API key",
)
async def create_api_key(
    body: DeveloperAPIKeyCreate,
    current_user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> DeveloperAPIKeyCreateResponse:
    if current_user.tenant_id is None:
        raise ForbiddenError("Cannot issue developer API keys without a tenant")

    full_key, key_prefix, hashed_key = generate_api_key()
    api_key = APIKey(
        user_id=current_user.user_id,
        tenant_id=current_user.tenant_id,
        key_prefix=key_prefix,
        hashed_key=hashed_key,
        name=body.name,
        description=body.description,
        is_active=True,
    )
    db.add(api_key)
    await db.flush()  # populate api_key.id

    for scope in body.scopes:
        db.add(DeveloperAPIKeyScope(api_key_id=api_key.id, scope=scope))

    await db.commit()
    await db.refresh(api_key)

    return DeveloperAPIKeyCreateResponse(
        id=str(api_key.id),
        name=api_key.name,
        description=api_key.description,
        key_prefix=api_key.key_prefix,
        is_active=api_key.is_active,
        scopes=list(body.scopes),
        created_at=api_key.created_at,
        updated_at=api_key.updated_at,
        full_key=full_key,
    )


@router.get(
    "/keys",
    response_model=DeveloperAPIKeyListResponse,
    summary="List developer API keys for the caller's tenant",
)
async def list_api_keys(
    current_user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
    include_inactive: bool = Query(default=False),
) -> DeveloperAPIKeyListResponse:
    if current_user.tenant_id is None and not current_user.is_super_admin():
        raise ForbiddenError("No tenant context")

    conditions = []
    if current_user.tenant_id is not None:
        conditions.append(APIKey.tenant_id == current_user.tenant_id)
    if not include_inactive:
        conditions.append(APIKey.is_active.is_(True))

    stmt = select(APIKey)
    if conditions:
        stmt = stmt.where(*conditions)
    stmt = stmt.order_by(desc(APIKey.created_at))

    result = await db.execute(stmt)
    keys = list(result.scalars().all())

    responses: list[DeveloperAPIKeyResponse] = []
    for k in keys:
        scopes = await _load_key_scopes(db, k.id)
        responses.append(
            DeveloperAPIKeyResponse(
                id=str(k.id),
                name=k.name,
                description=k.description,
                key_prefix=k.key_prefix,
                is_active=k.is_active,
                scopes=_scopes_to_response(scopes),
                created_at=k.created_at,
                updated_at=k.updated_at,
            )
        )

    return DeveloperAPIKeyListResponse(keys=responses, total=len(responses))


@router.delete(
    "/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke (deactivate) an API key",
)
async def revoke_api_key(
    key_id: uuid.UUID,
    current_user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> None:
    stmt = select(APIKey).where(APIKey.id == key_id)
    result = await db.execute(stmt)
    key = result.scalar_one_or_none()
    if key is None:
        raise NotFoundError("API key not found")
    if not current_user.is_super_admin() and key.tenant_id != current_user.tenant_id:
        raise ForbiddenError("API key belongs to a different tenant")

    key.is_active = False
    await db.commit()


@router.patch(
    "/keys/{key_id}/scopes",
    response_model=DeveloperAPIKeyResponse,
    summary="Replace the scopes on an API key",
)
async def update_api_key_scopes(
    key_id: uuid.UUID,
    body: DeveloperAPIKeyScopesUpdate,
    current_user: AuthUser,
    db: AsyncSession = Depends(get_db_session),
) -> DeveloperAPIKeyResponse:
    stmt = select(APIKey).where(APIKey.id == key_id)
    result = await db.execute(stmt)
    key = result.scalar_one_or_none()
    if key is None:
        raise NotFoundError("API key not found")
    if not current_user.is_super_admin() and key.tenant_id != current_user.tenant_id:
        raise ForbiddenError("API key belongs to a different tenant")

    # Replace all scopes
    existing = await _load_key_scopes(db, key.id)
    for s in existing:
        await db.delete(s)
    for new_scope in body.scopes:
        db.add(DeveloperAPIKeyScope(api_key_id=key.id, scope=new_scope))
    await db.commit()
    await db.refresh(key)

    scopes = await _load_key_scopes(db, key.id)
    return DeveloperAPIKeyResponse(
        id=str(key.id),
        name=key.name,
        description=key.description,
        key_prefix=key.key_prefix,
        is_active=key.is_active,
        scopes=_scopes_to_response(scopes),
        created_at=key.created_at,
        updated_at=key.updated_at,
    )


# ──────────────────────────────────────────────────────────────────────
# Event Definitions
# ──────────────────────────────────────────────────────────────────────


@router.post(
    "/event-types",
    response_model=EventDefinitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Define a new event type",
)
async def create_event_type(
    body: EventDefinitionCreate,
    principal: EventTypeWriter,
    db: AsyncSession = Depends(get_db_session),
) -> EventDefinitionResponse:
    definition = DeveloperEventDefinition(
        tenant_id=principal.tenant_id,
        name=body.name,
        description=body.description,
        schema=body.schema,
        default_severity=body.default_severity,
        created_by_user_id=principal.user_id,
    )
    db.add(definition)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Event type {body.name!r} already exists",
        )
    await db.refresh(definition)

    return EventDefinitionResponse(
        id=str(definition.id),
        name=definition.name,
        description=definition.description,
        schema=definition.schema,
        is_enabled=definition.is_enabled,
        default_severity=definition.default_severity,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


@router.get(
    "/event-types",
    response_model=EventDefinitionListResponse,
    summary="List event types for the caller's tenant",
)
async def list_event_types(
    principal: EventTypeReader,
    db: AsyncSession = Depends(get_db_session),
    include_disabled: bool = Query(default=False),
) -> EventDefinitionListResponse:
    conditions = [DeveloperEventDefinition.tenant_id == principal.tenant_id]
    if not include_disabled:
        conditions.append(DeveloperEventDefinition.is_enabled.is_(True))

    stmt = (
        select(DeveloperEventDefinition).where(*conditions).order_by(DeveloperEventDefinition.name)
    )
    result = await db.execute(stmt)
    defs = list(result.scalars().all())

    return EventDefinitionListResponse(
        definitions=[
            EventDefinitionResponse(
                id=str(d.id),
                name=d.name,
                description=d.description,
                schema=d.schema,
                is_enabled=d.is_enabled,
                default_severity=d.default_severity,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
            for d in defs
        ],
        total=len(defs),
    )


@router.patch(
    "/event-types/{definition_id}",
    response_model=EventDefinitionResponse,
    summary="Update an event type",
)
async def update_event_type(
    definition_id: uuid.UUID,
    body: EventDefinitionUpdate,
    principal: EventTypeWriter,
    db: AsyncSession = Depends(get_db_session),
) -> EventDefinitionResponse:
    stmt = select(DeveloperEventDefinition).where(
        DeveloperEventDefinition.id == definition_id,
        DeveloperEventDefinition.tenant_id == principal.tenant_id,
    )
    result = await db.execute(stmt)
    definition = result.scalar_one_or_none()
    if definition is None:
        raise NotFoundError("Event type not found")

    if body.description is not None:
        definition.description = body.description
    if body.schema is not None:
        definition.schema = body.schema
    if body.is_enabled is not None:
        definition.is_enabled = body.is_enabled
    if body.default_severity is not None:
        definition.default_severity = body.default_severity

    await db.commit()
    await db.refresh(definition)

    return EventDefinitionResponse(
        id=str(definition.id),
        name=definition.name,
        description=definition.description,
        schema=definition.schema,
        is_enabled=definition.is_enabled,
        default_severity=definition.default_severity,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


@router.delete(
    "/event-types/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an event type",
)
async def delete_event_type(
    definition_id: uuid.UUID,
    principal: EventTypeWriter,
    db: AsyncSession = Depends(get_db_session),
) -> None:
    stmt = select(DeveloperEventDefinition).where(
        DeveloperEventDefinition.id == definition_id,
        DeveloperEventDefinition.tenant_id == principal.tenant_id,
    )
    result = await db.execute(stmt)
    definition = result.scalar_one_or_none()
    if definition is None:
        raise NotFoundError("Event type not found")
    await db.delete(definition)
    await db.commit()


# ──────────────────────────────────────────────────────────────────────
# Event Ingestion
# ──────────────────────────────────────────────────────────────────────


@router.post(
    "/events",
    response_model=EventIngestResponse,
    summary="Ingest a batch of events",
    description=(
        "Accepts up to 500 events per request. Each event is matched against "
        "the tenant's registered event definitions — events whose `event_type` "
        "does not correspond to an enabled definition are skipped and reported "
        "in `skipped_reasons`. Use this for fire-and-forget telemetry; the "
        "Atlas AI dashboard surfaces ingested events under Developer > Events."
    ),
)
async def ingest_events(
    body: EventIngestRequest,
    principal: EventWriter,
    db: AsyncSession = Depends(get_db_session),
) -> EventIngestResponse:
    # Load enabled definitions for this tenant so we can validate event types
    stmt = select(
        DeveloperEventDefinition.name,
        DeveloperEventDefinition.default_severity,
    ).where(
        DeveloperEventDefinition.tenant_id == principal.tenant_id,
        DeveloperEventDefinition.is_enabled.is_(True),
    )
    result = await db.execute(stmt)
    definitions: dict[str, EventSeverity] = {row[0]: row[1] for row in result.all()}

    ingested = 0
    skipped = 0
    reasons: list[str] = []

    for item in body.events:
        if item.event_type not in definitions:
            skipped += 1
            reasons.append(f"event_type {item.event_type!r} not registered or disabled")
            continue

        severity = item.severity or definitions[item.event_type]
        occurred = item.occurred_at or datetime.now(UTC)

        db.add(
            DeveloperEvent(
                tenant_id=principal.tenant_id,
                event_type=item.event_type,
                severity=severity,
                occurred_at=occurred,
                payload=item.payload,
                source=item.source,
                session_id=item.session_id,
                user_external_id=item.user_external_id,
                correlation_id=item.correlation_id,
                api_key_id=principal.api_key_id,
                occurrences=item.occurrences,
            )
        )
        ingested += 1

    await db.commit()

    return EventIngestResponse(
        ingested=ingested,
        skipped=skipped,
        skipped_reasons=reasons,
    )


# ──────────────────────────────────────────────────────────────────────
# Event Review (dashboard)
# ──────────────────────────────────────────────────────────────────────


@router.get(
    "/events",
    response_model=EventListResponse,
    summary="List ingested events for the caller's tenant",
)
async def list_events(
    principal: EventReader,
    db: AsyncSession = Depends(get_db_session),
    event_type: str | None = Query(default=None),
    severity: EventSeverity | None = Query(default=None),
    source: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    correlation_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    after: uuid.UUID | None = Query(
        default=None,
        description="Cursor — ID of the last item from the previous page",
    ),
) -> EventListResponse:
    conditions = [DeveloperEvent.tenant_id == principal.tenant_id]
    if event_type:
        conditions.append(DeveloperEvent.event_type == event_type)
    if severity:
        conditions.append(DeveloperEvent.severity == severity)
    if source:
        conditions.append(DeveloperEvent.source == source)
    if session_id:
        conditions.append(DeveloperEvent.session_id == session_id)
    if correlation_id:
        conditions.append(DeveloperEvent.correlation_id == correlation_id)
    if since:
        conditions.append(DeveloperEvent.occurred_at >= since)
    if until:
        conditions.append(DeveloperEvent.occurred_at <= until)
    if after:
        # Cursor-based on (occurred_at, id) — simple form: filter id
        conditions.append(DeveloperEvent.id < after)

    count_stmt = select(func.count(DeveloperEvent.id)).where(*conditions)
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = (
        select(DeveloperEvent)
        .where(*conditions)
        .order_by(desc(DeveloperEvent.occurred_at), desc(DeveloperEvent.id))
        .limit(limit)
    )
    result = await db.execute(stmt)
    events = list(result.scalars().all())

    next_cursor = str(events[-1].id) if len(events) == limit else None

    return EventListResponse(
        events=[
            EventResponse(
                id=str(e.id),
                event_type=e.event_type,
                severity=e.severity,
                occurred_at=e.occurred_at,
                payload=e.payload,
                source=e.source,
                session_id=e.session_id,
                user_external_id=e.user_external_id,
                correlation_id=e.correlation_id,
                occurrences=e.occurrences,
                created_at=e.created_at,
            )
            for e in events
        ],
        total=total,
        has_more=len(events) == limit and total > limit,
        next_cursor=next_cursor,
    )


@router.get(
    "/events/{event_id}",
    response_model=EventResponse,
    summary="Get a single event",
)
async def get_event(
    event_id: uuid.UUID,
    principal: EventReader,
    db: AsyncSession = Depends(get_db_session),
) -> EventResponse:
    stmt = select(DeveloperEvent).where(
        DeveloperEvent.id == event_id,
        DeveloperEvent.tenant_id == principal.tenant_id,
    )
    result = await db.execute(stmt)
    event = result.scalar_one_or_none()
    if event is None:
        raise NotFoundError("Event not found")

    return EventResponse(
        id=str(event.id),
        event_type=event.event_type,
        severity=event.severity,
        occurred_at=event.occurred_at,
        payload=event.payload,
        source=event.source,
        session_id=event.session_id,
        user_external_id=event.user_external_id,
        correlation_id=event.correlation_id,
        occurrences=event.occurrences,
        created_at=event.created_at,
    )
