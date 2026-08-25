"""Cost ledger router — manual "Sync now" + all-tenant cron sweep."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import structlog
from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthUser, get_tenant_db_session
from app.config import get_settings
from app.database import get_standalone_session, set_tenant_guc
from app.errors import AppException, NotFoundError
from app.models.ai_cost_record import (
    AICostRecord,
    CostProvider,
    CostSource,
    CostSubjectKind,
)
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.tenant import Tenant
from app.schemas.cost import (
    BreakdownRow,
    CronSyncItem,
    CronSyncResponse,
    SummaryResponse,
    SyncResponse,
    TimeseriesPoint,
)
from app.services.cost.sync_service import _cost_provider_for, sync_integration

logger = structlog.get_logger()

router = APIRouter(prefix="/cost", tags=["Cost"])

# How far back a single sync sweeps. Vendors restate recent days, so we
# re-pull a small trailing window each time and rely on idempotent upsert.
_SYNC_LOOKBACK_DAYS = 2

# The IntegrationProviders that map onto a CostProvider — i.e. the providers
# the cost ledger knows how to sync. Derived from the enums so the two never
# drift apart.
_COST_PROVIDERS: list[IntegrationProvider] = [
    p for p in IntegrationProvider if p.value.lower() in CostProvider._value2member_map_
]


def _sync_window() -> tuple[date, date]:
    until = datetime.now(UTC).date()
    return until - timedelta(days=_SYNC_LOOKBACK_DAYS), until


@router.post(
    "/integrations/{integration_id}/sync",
    response_model=SyncResponse,
)
async def sync_now(
    integration_id: uuid.UUID,
    user: AuthUser,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> SyncResponse:
    """Manually sync one cost integration over a short trailing window.

    404 if the integration id is unknown to the caller's tenant; 400 if the
    integration exists but its provider is not a cost provider.
    """
    query = select(Integration).where(Integration.id == integration_id)
    if not user.is_super_admin():
        query = query.where(Integration.tenant_id == user.tenant_id)

    integration = (await db.execute(query)).scalar_one_or_none()
    if integration is None:
        raise NotFoundError("Integration", str(integration_id))

    try:
        _cost_provider_for(integration)
    except ValueError as exc:
        raise AppException(
            code="NOT_A_COST_PROVIDER",
            message=str(exc),
            status_code=400,
        ) from exc

    since, until = _sync_window()
    result = await sync_integration(db, integration, since, until)

    return SyncResponse(
        records_upserted=result.records_upserted,
        since=result.since,
        until=result.until,
        status=result.status.value,
        error=result.error,
    )


@router.post("/sync", response_model=CronSyncResponse)
async def cron_sync(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> CronSyncResponse:
    """All-tenant cost sweep, guarded by a shared secret (cron entry point).

    Not JWT-authenticated. Returns 503 when no secret is configured, 401 when
    the ``X-Cron-Secret`` header is missing or does not match. On success it
    enumerates every tenant, sets that tenant's GUC, loads its CONNECTED cost
    integrations (under RLS *and* an explicit tenant filter), and syncs each;
    one failing integration never aborts the sweep.
    """
    configured = get_settings().cost_sync_cron_secret
    if not configured:
        raise AppException(
            code="CRON_NOT_CONFIGURED",
            message="cron sync not configured",
            status_code=503,
        )
    if x_cron_secret is None or not hmac.compare_digest(x_cron_secret, configured):
        raise AppException(
            code="UNAUTHORIZED",
            message="invalid cron secret",
            status_code=401,
        )

    since, until = _sync_window()
    results: list[CronSyncItem] = []

    async with get_standalone_session() as db:
        # ``grc.integrations`` enforces RLS, so a no-GUC session sees zero
        # integration rows. ``grc.tenants`` has no RLS, so we CAN enumerate
        # every tenant here, then re-scope per tenant to make that tenant's
        # integrations visible (and to satisfy the RLS writes inside
        # ``sync_integration``). Defense in depth: each per-tenant load also
        # carries an explicit ``tenant_id`` filter — we never rely on RLS
        # alone.
        tenant_ids = (await db.execute(select(Tenant.id))).scalars().all()

        for tid in tenant_ids:
            # Make this tenant's integrations visible under RLS, then collect
            # just their ids. We load ids (not ORM rows) because each
            # ``sync_integration`` commit ends the transaction — which both
            # clears the transaction-local tenant GUC and expires ORM objects.
            await set_tenant_guc(db, tid)
            integration_ids = list(
                (
                    await db.execute(
                        select(Integration.id).where(
                            Integration.tenant_id == tid,
                            Integration.provider.in_(_COST_PROVIDERS),
                            Integration.status == IntegrationStatus.CONNECTED,
                        )
                    )
                )
                .scalars()
                .all()
            )

            for integration_id in integration_ids:
                # Re-assert the GUC before EACH integration: the previous
                # ``sync_integration`` committed, clearing the transaction-local
                # ``app.current_tenant_id``. Without this, the 2nd+ integration
                # of a tenant would read zero rows and have its inserts rejected
                # by the ai_cost_records RLS WITH CHECK. Then re-load the row
                # fresh under the active GUC.
                await set_tenant_guc(db, tid)
                integration = await db.get(Integration, integration_id)
                if integration is None:
                    continue
                try:
                    result = await sync_integration(db, integration, since, until)
                    results.append(
                        CronSyncItem(
                            integration_id=str(integration.id),
                            status=result.status.value,
                            records_upserted=result.records_upserted,
                            error=result.error,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — one bad row must not abort.
                    logger.warning(
                        "cron_sync_integration_failed",
                        integration_id=str(integration_id),
                        tenant_id=str(tid),
                        error=str(exc),
                    )
                    results.append(
                        CronSyncItem(
                            integration_id=str(integration_id),
                            status=IntegrationStatus.ERROR.value,
                            records_upserted=0,
                            error=str(exc)[:500],
                        )
                    )

    return CronSyncResponse(
        integrations_synced=len(results),
        results=results,
    )


# ─── Aggregate read endpoints ────────────────────────────────────────

# The cost_source values that count as "derived" (vs vendor_reported).
_DERIVED_SOURCES = (CostSource.derived_tokens, CostSource.derived_seats)

# Allowed ``breakdown?by=`` dimensions.
_BREAKDOWN_BY = {"provider", "model", "member"}


def _scope_to_tenant(query, user: AuthUser):  # type: ignore[no-untyped-def]
    """Apply an explicit tenant filter for non-super-admins.

    RLS already scopes rows to the tenant GUC, but — consistent with the
    manual-sync endpoint — we never rely on RLS alone.
    """
    if not user.is_super_admin():
        query = query.where(AICostRecord.tenant_id == user.tenant_id)
    return query


def _window_filters(query, since: date, until: date, provider: CostProvider | None):  # type: ignore[no-untyped-def]
    """Apply the inclusive date window + optional provider filter."""
    query = query.where(
        AICostRecord.usage_date >= since,
        AICostRecord.usage_date <= until,
    )
    if provider is not None:
        query = query.where(AICostRecord.provider == provider)
    return query


@router.get("/summary", response_model=SummaryResponse)
async def cost_summary(
    user: AuthUser,
    since: date = Query(...),
    until: date = Query(...),
    provider: CostProvider | None = Query(default=None),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> SummaryResponse:
    """Roll up cost over the window for the caller's tenant."""
    cost = AICostRecord.cost_usd
    query = select(
        func.coalesce(func.sum(cost), 0),
        func.coalesce(
            func.sum(cost).filter(AICostRecord.cost_source == CostSource.vendor_reported),
            0,
        ),
        func.coalesce(
            func.sum(cost).filter(AICostRecord.cost_source.in_(_DERIVED_SOURCES)),
            0,
        ),
        func.coalesce(
            func.sum(cost).filter(AICostRecord.is_provisional.is_(True)),
            0,
        ),
        func.count(func.distinct(AICostRecord.integration_id)),
    )
    query = _window_filters(_scope_to_tenant(query, user), since, until, provider)

    total, vendor, derived, provisional, connectors = (await db.execute(query)).one()
    return SummaryResponse(
        total_cost_usd=Decimal(str(total)),
        vendor_reported_usd=Decimal(str(vendor)),
        derived_usd=Decimal(str(derived)),
        provisional_usd=Decimal(str(provisional)),
        active_connectors=int(connectors),
    )


@router.get("/timeseries", response_model=list[TimeseriesPoint])
async def cost_timeseries(
    user: AuthUser,
    since: date = Query(...),
    until: date = Query(...),
    provider: CostProvider | None = Query(default=None),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> list[TimeseriesPoint]:
    """One spend bucket per ``usage_date`` in range, ordered ascending.

    ``is_provisional`` is true when ANY row that day is provisional. We use
    ``max(cast(is_provisional, Integer))`` so the bool aggregate is portable
    to SQLite (which has no ``bool_or``).
    """
    query = select(
        AICostRecord.usage_date,
        func.coalesce(func.sum(AICostRecord.cost_usd), 0),
        func.max(cast(AICostRecord.is_provisional, Integer)),
    )
    query = _window_filters(_scope_to_tenant(query, user), since, until, provider)
    query = query.group_by(AICostRecord.usage_date).order_by(AICostRecord.usage_date.asc())

    rows = (await db.execute(query)).all()
    return [
        TimeseriesPoint(
            date=usage_date,
            cost_usd=Decimal(str(total)),
            is_provisional=bool(prov),
        )
        for usage_date, total, prov in rows
    ]


@router.get("/breakdown", response_model=list[BreakdownRow])
async def cost_breakdown(
    user: AuthUser,
    since: date = Query(...),
    until: date = Query(...),
    by: str = Query(...),
    provider: CostProvider | None = Query(default=None),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> list[BreakdownRow]:
    """Spend grouped by ``provider`` | ``model`` | ``member``, ranked desc.

    ``by=provider`` groups on ``provider``; ``by=model`` / ``by=member`` group
    on ``subject_ref`` restricted to the matching ``subject_kind``. The row's
    ``cost_source`` is the group's single source, or ``"mixed"`` when the group
    spans more than one.
    """
    if by not in _BREAKDOWN_BY:
        raise AppException(
            code="INVALID_BREAKDOWN_BY",
            message=f"'by' must be one of {sorted(_BREAKDOWN_BY)}",
            status_code=400,
        )

    key_col = AICostRecord.provider if by == "provider" else AICostRecord.subject_ref

    query = select(
        key_col,
        func.coalesce(func.sum(AICostRecord.cost_usd), 0),
        func.count(func.distinct(AICostRecord.cost_source)),
        func.min(AICostRecord.cost_source),
    )
    query = _window_filters(_scope_to_tenant(query, user), since, until, provider)

    if by == "model":
        query = query.where(AICostRecord.subject_kind == CostSubjectKind.model)
    elif by == "member":
        query = query.where(AICostRecord.subject_kind == CostSubjectKind.member)

    query = query.group_by(key_col).order_by(
        func.coalesce(func.sum(AICostRecord.cost_usd), 0).desc()
    )

    rows = (await db.execute(query)).all()

    result: list[BreakdownRow] = []
    for key, total, n_sources, one_source in rows:
        key_str = key.value if isinstance(key, CostProvider) else str(key)
        if int(n_sources) > 1:
            source = "mixed"
        else:
            source = one_source.value if isinstance(one_source, CostSource) else str(one_source)
        result.append(
            BreakdownRow(
                key=key_str,
                cost_usd=Decimal(str(total)),
                cost_source=source,
            )
        )
    return result
