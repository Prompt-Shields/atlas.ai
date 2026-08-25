"""Tenant drift signal CRUD — `/api/v1/drift-signals`.

OrgAdmin-only surface for managing the tenant's drift overrides
(model deprecations + tool rebrands). Combined with the curated
globals in `app.services.drift_signals` to drive the drift
dispatcher.

Endpoints:
  GET    /              List active signals for this tenant.
  POST   /              Create a new signal.
  PATCH  /{id}          Update label / severity / is_active / etc.
  DELETE /{id}          Hard-delete.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import OrgAdmin
from app.database import get_db_session
from app.errors import ConflictError, ForbiddenError, NotFoundError
from app.models.drift_signal import (
    TenantDriftSignal,
    TenantDriftSignalKind,
    TenantDriftSignalSeverity,
)

router = APIRouter(prefix="/drift-signals", tags=["Drift signals"])


# ─── Schemas ─────────────────────────────────────────────────────────


class DriftSignalCreate(BaseModel):
    kind: TenantDriftSignalKind
    match_pattern: str = Field(..., min_length=1, max_length=100)
    label: str = Field(..., min_length=1, max_length=150)
    severity: TenantDriftSignalSeverity = TenantDriftSignalSeverity.MEDIUM
    successor_or_current_name: str | None = Field(None, max_length=255)
    effective_as_of: str | None = Field(None, max_length=20)


class DriftSignalUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=150)
    severity: TenantDriftSignalSeverity | None = None
    successor_or_current_name: str | None = Field(None, max_length=255)
    effective_as_of: str | None = Field(None, max_length=20)
    is_active: bool | None = None


class DriftSignalResponse(BaseModel):
    id: str
    tenant_id: str
    kind: TenantDriftSignalKind
    match_pattern: str
    label: str
    severity: TenantDriftSignalSeverity
    successor_or_current_name: str | None
    effective_as_of: str | None
    is_active: bool
    created_by_user_id: str | None


class DriftSignalListResponse(BaseModel):
    signals: list[DriftSignalResponse]
    total: int


def _serialise(row: TenantDriftSignal) -> DriftSignalResponse:
    return DriftSignalResponse(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        kind=row.kind,
        match_pattern=row.match_pattern,
        label=row.label,
        severity=row.severity,
        successor_or_current_name=row.successor_or_current_name,
        effective_as_of=row.effective_as_of,
        is_active=row.is_active,
        created_by_user_id=(str(row.created_by_user_id) if row.created_by_user_id else None),
    )


# ─── Endpoints ───────────────────────────────────────────────────────


@router.get("", response_model=DriftSignalListResponse)
async def list_signals(
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> DriftSignalListResponse:
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant.")
    rows = (
        (
            await db.execute(
                select(TenantDriftSignal)
                .where(TenantDriftSignal.tenant_id == user.tenant_id)
                .order_by(TenantDriftSignal.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return DriftSignalListResponse(
        signals=[_serialise(r) for r in rows],
        total=len(rows),
    )


@router.post(
    "",
    response_model=DriftSignalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_signal(
    payload: DriftSignalCreate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_db_session),
) -> DriftSignalResponse:
    if user.tenant_id is None:
        raise ForbiddenError("User has no tenant.")
    row = TenantDriftSignal(
        tenant_id=user.tenant_id,
        kind=payload.kind,
        match_pattern=payload.match_pattern.strip(),
        label=payload.label.strip(),
        severity=payload.severity,
        successor_or_current_name=((payload.successor_or_current_name or "").strip() or None),
        effective_as_of=((payload.effective_as_of or "").strip() or None),
        created_by_user_id=user.user_id,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError(
            f"A drift signal with kind={payload.kind.value} and "
            f"match_pattern={payload.match_pattern!r} already exists."
        )
    await db.refresh(row)
    return _serialise(row)


@router.patch("/{signal_id}", response_model=DriftSignalResponse)
async def update_signal(
    payload: DriftSignalUpdate,
    user: OrgAdmin,
    signal_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> DriftSignalResponse:
    row = await _load(db, signal_id, user.tenant_id)

    if payload.label is not None:
        row.label = payload.label.strip()
    if payload.severity is not None:
        row.severity = payload.severity
    if payload.successor_or_current_name is not None:
        row.successor_or_current_name = payload.successor_or_current_name.strip() or None
    if payload.effective_as_of is not None:
        row.effective_as_of = payload.effective_as_of.strip() or None
    if payload.is_active is not None:
        row.is_active = payload.is_active

    await db.commit()
    await db.refresh(row)
    return _serialise(row)


@router.delete("/{signal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_signal(
    user: OrgAdmin,
    signal_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    row = await _load(db, signal_id, user.tenant_id)
    await db.delete(row)
    await db.commit()


# ─── Helpers ─────────────────────────────────────────────────────────


async def _load(
    db: AsyncSession,
    signal_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
) -> TenantDriftSignal:
    if tenant_id is None:
        raise ForbiddenError("User has no tenant.")
    row = (
        await db.execute(
            select(TenantDriftSignal).where(
                TenantDriftSignal.id == signal_id,
                TenantDriftSignal.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("TenantDriftSignal", str(signal_id))
    return row
