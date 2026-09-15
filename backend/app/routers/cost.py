"""Cost ledger router — manual "Sync now" + all-tenant cron sweep."""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    AuthUser,
    OrgAdmin,
    get_tenant_db_session,
    require_api_key,
)
from app.config import get_settings
from app.database import get_db_session, get_standalone_session, set_tenant_guc
from app.errors import AppException, NotFoundError
from app.models.ai_cost_record import (
    AICostRecord,
    CostProvider,
    CostSource,
    CostSubjectKind,
    SelfHostedCostProvider,
)
from app.models.cost_anomaly import CostAnomaly
from app.models.cost_budget import CostBudget
from app.models.integration import (
    Integration,
    IntegrationProvider,
    IntegrationStatus,
)
from app.models.roi_assumptions import HoursSavedSource, RoiAssumptions
from app.models.tenant import Tenant
from app.models.user import APIKey
from app.schemas.cost import (
    AnomalyPayload,
    BreakdownRow,
    BudgetPayload,
    BudgetUpsert,
    CronSyncItem,
    CronSyncResponse,
    RoiAssumptionsPayload,
    RoiAssumptionsUpdate,
    RoiResponse,
    SelfHostedUsageBatch,
    SelfHostedUsageIngestResponse,
    SummaryResponse,
    SyncResponse,
    TimeseriesPoint,
)
from app.services.audit import log_audit_event
from app.services.cost.anomalies import detect_and_alert
from app.services.cost.budgets import (
    BudgetStatus,
    dispatch_budget_alerts,
    evaluate_budget,
    load_budgets,
)
from app.services.cost.roi import build_roi, get_or_default_assumptions
from app.services.cost.self_hosted_ingest import ingest_usage
from app.services.cost.sync_service import _cost_provider_for, sync_integration

logger = structlog.get_logger()

router = APIRouter(prefix="/cost", tags=["Cost"])

# How far back a single sync sweeps. Vendors restate recent days, so we
# re-pull a small trailing window each time and rely on idempotent upsert.
_SYNC_LOOKBACK_DAYS = 2

# The IntegrationProviders the cost ledger knows how to *pull* from. Derived
# from the enums so the two never drift apart, minus the push-mode providers.
#
# That subtraction matters: a self-hosted integration row is auto-provisioned
# by the first pushed batch (see services/cost/self_hosted_ingest.py), and it
# has no connector to fetch from. Left in this list the cron sweep would try to
# sync it every run, fail, and flip the status to ERROR — undoing the CONNECTED
# the push had just set, on a loop, for an integration that is working fine.
_PUSH_ONLY_COST_PROVIDERS: frozenset[str] = frozenset(p.value for p in SelfHostedCostProvider)
_COST_PROVIDERS: list[IntegrationProvider] = [
    p
    for p in IntegrationProvider
    if p.value.lower() in CostProvider._value2member_map_
    and p.value.lower() not in _PUSH_ONLY_COST_PROVIDERS
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

        # Budgets are evaluated after every tenant has synced, not inside the
        # loop above: a budget must be judged against the month's finished
        # numbers, and alerting mid-sweep would compare this month's spend to
        # a ledger still missing the connector being synced two lines later.
        for tid in tenant_ids:
            await set_tenant_guc(db, tid)
            try:
                await dispatch_budget_alerts(db, tid)
            except Exception as exc:  # noqa: BLE001 — alerting never aborts the sweep.
                logger.warning(
                    "cron_budget_alerts_failed",
                    tenant_id=str(tid),
                    error=str(exc),
                )
            # Anomaly detection runs in the same pass but its own try: a
            # budget-alert failure must not cost the tenant its spike
            # detection, and vice versa.
            await set_tenant_guc(db, tid)
            try:
                await detect_and_alert(db, tid)
            except Exception as exc:  # noqa: BLE001 — detection never aborts the sweep.
                logger.warning(
                    "cron_anomaly_detection_failed",
                    tenant_id=str(tid),
                    error=str(exc),
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


@router.post("/usage", response_model=SelfHostedUsageIngestResponse)
async def ingest_self_hosted_usage(
    payload: SelfHostedUsageBatch,
    api_key_record: Annotated[APIKey, Depends(require_api_key)],
    db: AsyncSession = Depends(get_db_session),
) -> SelfHostedUsageIngestResponse:
    """Report token usage from a self-hosted AI app (cost-ledger slice 2).

    X-API-Key authenticated, like the prompt-telemetry ingest: the caller is a
    customer's own service, not a browser session, so there is no JWT to carry.
    The key's tenant decides which ledger the usage lands in — the payload
    cannot name a tenant.

    Cost is **derived** from tokens via the price book and tagged
    `derived_tokens`; the dollars themselves are on the customer's cloud bill.
    Models with no price still have their tokens recorded and come back in
    `unpriced_models` rather than being costed at zero, which would understate
    spend while looking authoritative.

    Idempotent on `batch_id`. Unlike the pull connectors this **accumulates**
    into the day's row, so a retried batch would otherwise double-count; a
    replay returns the original result with `duplicate=true`.
    """
    if api_key_record.tenant_id is None:
        raise AppException(
            code="API_KEY_NOT_TENANT_SCOPED",
            message="API key must be tenant-scoped to report usage",
            status_code=403,
        )

    # The ledger and batch tables are tenant-scoped under RLS, and this request
    # arrives with no JWT to drive get_tenant_db_session, so set the GUC from
    # the key's tenant explicitly.
    await set_tenant_guc(db, api_key_record.tenant_id)

    result = await ingest_usage(
        db,
        tenant_id=api_key_record.tenant_id,
        provider=payload.provider,
        batch_id=payload.batch_id,
        records=payload.records,
    )

    return SelfHostedUsageIngestResponse(
        batch_id=payload.batch_id,
        accepted_calls=result.accepted_calls,
        skipped_calls=result.skipped_calls,
        rows_touched=result.rows_touched,
        cost_usd=result.cost_usd,
        unpriced_models=result.unpriced_models,
        duplicate=result.duplicate,
    )


# ─── ROI (cost-ledger slice 3) ───────────────────────────────────────


def _assumptions_payload(assumptions: RoiAssumptions, *, is_default: bool) -> RoiAssumptionsPayload:
    return RoiAssumptionsPayload(
        blended_hourly_rate_usd=Decimal(str(assumptions.blended_hourly_rate_usd)),
        hours_saved_source=assumptions.hours_saved_source,
        manual_hours_saved_per_month=(
            Decimal(str(assumptions.manual_hours_saved_per_month))
            if assumptions.manual_hours_saved_per_month is not None
            else None
        ),
        updated_at=assumptions.updated_at if not is_default else None,
        updated_by_user_id=(
            str(assumptions.updated_by_user_id)
            if assumptions.updated_by_user_id is not None
            else None
        ),
        is_default=is_default,
    )


@router.get("/roi", response_model=RoiResponse)
async def cost_roi(
    user: AuthUser,
    since: date = Query(...),
    until: date = Query(...),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> RoiResponse:
    """AI ROI for the caller's tenant over a window.

    Combines the measured ledger total with an estimated human-equivalent
    value. The estimate's provenance travels with the answer: `basis` says what
    the hours-saved figure rests on, and `is_illustrative` is set whenever that
    is anything other than this tenant's own measured usage.

    `roi_multiplier` is null when there was no spend in the window — the ratio
    is undefined, and any stand-in for it would be a flattering fiction.
    """
    if until < since:
        raise AppException(
            code="INVALID_WINDOW",
            message="until must not precede since",
            status_code=422,
        )

    result = await build_roi(db, tenant_id=user.tenant_id, since=since, until=until)
    return RoiResponse(
        window_start=result.window_start,
        window_end=result.window_end,
        window_days=result.window_days,
        ai_spend_usd=result.ai_spend_usd,
        hours_saved_per_month=result.hours_saved_per_month,
        hours_saved_in_window=result.hours_saved_in_window,
        blended_hourly_rate_usd=result.blended_hourly_rate_usd,
        human_value_usd=result.human_value_usd,
        net_value_usd=result.net_value_usd,
        roi_multiplier=result.roi_multiplier,
        basis=result.basis,
        basis_detail=result.basis_detail,
        is_illustrative=result.is_illustrative,
    )


@router.get("/roi/assumptions", response_model=RoiAssumptionsPayload)
async def get_roi_assumptions(
    user: AuthUser,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> RoiAssumptionsPayload:
    """The tenant's human-cost model, or the defaults if it has never set one.

    Readable by any authenticated user: the assumptions are the footnote to a
    number the whole organisation sees, and hiding them from the people reading
    the headline would defeat the point of storing them.
    """
    stored = (
        await db.execute(select(RoiAssumptions).where(RoiAssumptions.tenant_id == user.tenant_id))
    ).scalar_one_or_none()
    if stored is not None:
        return _assumptions_payload(stored, is_default=False)

    return _assumptions_payload(
        await get_or_default_assumptions(db, user.tenant_id), is_default=True
    )


@router.put("/roi/assumptions", response_model=RoiAssumptionsPayload)
async def put_roi_assumptions(
    payload: RoiAssumptionsUpdate,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> RoiAssumptionsPayload:
    """Set the human-cost model. Admin-only, and audited.

    Editing these numbers changes the ROI the whole organisation reads, so the
    change is written to the audit log with both the old and new values. An
    unexplained jump in the headline should be answerable from the record
    rather than from memory.
    """
    if (
        payload.hours_saved_source is HoursSavedSource.manual
        and payload.manual_hours_saved_per_month is None
    ):
        raise AppException(
            code="MANUAL_HOURS_REQUIRED",
            message=(
                "manual_hours_saved_per_month is required when hours_saved_source is 'manual'"
            ),
            status_code=422,
        )

    stored = (
        await db.execute(select(RoiAssumptions).where(RoiAssumptions.tenant_id == user.tenant_id))
    ).scalar_one_or_none()

    previous = (
        {
            "blended_hourly_rate_usd": str(stored.blended_hourly_rate_usd),
            "hours_saved_source": stored.hours_saved_source.value,
            "manual_hours_saved_per_month": (
                str(stored.manual_hours_saved_per_month)
                if stored.manual_hours_saved_per_month is not None
                else None
            ),
        }
        if stored is not None
        else None
    )

    if stored is None:
        stored = RoiAssumptions(tenant_id=user.tenant_id)
        db.add(stored)

    stored.blended_hourly_rate_usd = payload.blended_hourly_rate_usd
    stored.hours_saved_source = payload.hours_saved_source
    stored.manual_hours_saved_per_month = payload.manual_hours_saved_per_month
    stored.updated_by_user_id = user.user_id

    await log_audit_event(
        db,
        event_type="cost.roi_assumptions.updated",
        action="update",
        actor_id=user.user_id,
        actor_email=user.email,
        tenant_id=user.tenant_id,
        resource_type="roi_assumptions",
        details={
            "previous": previous,
            "current": {
                "blended_hourly_rate_usd": str(payload.blended_hourly_rate_usd),
                "hours_saved_source": payload.hours_saved_source.value,
                "manual_hours_saved_per_month": (
                    str(payload.manual_hours_saved_per_month)
                    if payload.manual_hours_saved_per_month is not None
                    else None
                ),
            },
        },
    )

    await db.commit()
    await db.refresh(stored)
    return _assumptions_payload(stored, is_default=False)


def _budget_payload(budget: CostBudget, status: BudgetStatus) -> BudgetPayload:
    """Join a stored ceiling to this month's standing."""
    return BudgetPayload(
        id=budget.id,
        provider=budget.provider,
        amount_usd=budget.amount_usd,
        warn_threshold_percent=budget.warn_threshold_percent,
        alerts_enabled=budget.alerts_enabled,
        period_start=status.period_start,
        as_of=status.as_of,
        spend_usd=status.spend_usd,
        provisional_usd=status.provisional_usd,
        percent_used=status.percent_used,
        alert_level=status.alert_level,
        has_ledger_data=status.has_ledger_data,
        projected_month_end_usd=status.projected_month_end_usd,
        last_alerted_at=budget.last_alerted_at,
    )


@router.get("/budgets", response_model=list[BudgetPayload])
async def list_budgets(
    user: AuthUser,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> list[BudgetPayload]:
    """Every budget for the tenant with this month's spend against it.

    Readable by any authenticated user, matching the ROI assumptions: a ceiling
    is context for a number the whole organisation sees, and hiding it from the
    people reading the spend would defeat the point of setting it.
    """
    budgets = await load_budgets(db, user.tenant_id)
    return [_budget_payload(b, await evaluate_budget(db, b)) for b in budgets]


@router.put("/budgets", response_model=BudgetPayload)
async def upsert_budget(
    payload: BudgetUpsert,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> BudgetPayload:
    """Create or replace the budget for one scope. Admin-only, and audited.

    Upsert rather than separate POST/PATCH because the resource is identified
    by (tenant, provider) rather than by an id the caller has to fetch first —
    "set the Cursor budget to $500" is one call whether or not one already
    exists.

    Raising the ceiling clears the alert state. Otherwise a budget that alerted
    at 100% last week would stay silent after being doubled, which is the one
    moment the tenant most wants to hear from it again.
    """
    existing = (
        await db.execute(
            select(CostBudget).where(
                CostBudget.tenant_id == user.tenant_id,
                CostBudget.provider.is_(None)
                if payload.provider is None
                else CostBudget.provider == payload.provider,
            )
        )
    ).scalar_one_or_none()

    previous = (
        {
            "amount_usd": str(existing.amount_usd),
            "warn_threshold_percent": str(existing.warn_threshold_percent),
            "alerts_enabled": existing.alerts_enabled,
        }
        if existing is not None
        else None
    )

    if existing is None:
        existing = CostBudget(tenant_id=user.tenant_id, provider=payload.provider)
        db.add(existing)
    elif payload.amount_usd != existing.amount_usd:
        existing.last_alerted_period = None
        existing.last_alerted_level = None
        existing.last_alerted_at = None

    existing.amount_usd = payload.amount_usd
    existing.warn_threshold_percent = payload.warn_threshold_percent
    existing.alerts_enabled = payload.alerts_enabled
    existing.updated_by_user_id = user.user_id

    await log_audit_event(
        db,
        event_type="cost.budget.updated",
        action="update",
        actor_id=user.user_id,
        actor_email=user.email,
        tenant_id=user.tenant_id,
        resource_type="cost_budget",
        details={
            "provider": payload.provider.value if payload.provider else None,
            "previous": previous,
            "current": {
                "amount_usd": str(payload.amount_usd),
                "warn_threshold_percent": str(payload.warn_threshold_percent),
                "alerts_enabled": payload.alerts_enabled,
            },
        },
    )

    await db.commit()
    await db.refresh(existing)
    return _budget_payload(existing, await evaluate_budget(db, existing))


@router.delete("/budgets/{budget_id}", status_code=204)
async def delete_budget(
    budget_id: uuid.UUID,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> None:
    """Remove a budget. Admin-only, and audited.

    The explicit tenant filter is not redundant with RLS: without it a valid
    id from another tenant would be a cross-tenant delete if the GUC were ever
    unset, and we never rely on RLS alone.
    """
    budget = (
        await db.execute(
            select(CostBudget).where(
                CostBudget.id == budget_id,
                CostBudget.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if budget is None:
        raise NotFoundError("Budget not found")

    await log_audit_event(
        db,
        event_type="cost.budget.deleted",
        action="delete",
        actor_id=user.user_id,
        actor_email=user.email,
        tenant_id=user.tenant_id,
        resource_type="cost_budget",
        details={
            "provider": budget.provider.value if budget.provider else None,
            "amount_usd": str(budget.amount_usd),
        },
    )

    await db.delete(budget)
    await db.commit()


@router.get("/anomalies", response_model=list[AnomalyPayload])
async def list_anomalies(
    user: AuthUser,
    include_acknowledged: bool = Query(default=False),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> list[AnomalyPayload]:
    """Detected spend spikes, newest first.

    Unacknowledged only by default: the list is a to-do, and an acknowledged
    anomaly is done. `include_acknowledged=true` gets the full history, which
    is what you want when asking "has this happened before?" — the question
    that turns a one-off into a pattern.
    """
    query = select(CostAnomaly).where(CostAnomaly.tenant_id == user.tenant_id)
    if not include_acknowledged:
        query = query.where(CostAnomaly.acknowledged_at.is_(None))

    rows = (
        (await db.execute(query.order_by(CostAnomaly.usage_date.desc(), CostAnomaly.provider)))
        .scalars()
        .all()
    )
    return [AnomalyPayload.model_validate(r) for r in rows]


@router.post("/anomalies/{anomaly_id}/acknowledge", response_model=AnomalyPayload)
async def acknowledge_anomaly(
    anomaly_id: uuid.UUID,
    user: OrgAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> AnomalyPayload:
    """Mark a spike as explained. Admin-only, and audited.

    Acknowledging is not deleting: the row stays as history, and the detector
    reads the flag so a spike somebody has already accounted for is never
    raised again. Re-acknowledging an acknowledged row is a no-op rather than
    an error — two admins clicking the same button should not produce a 409.

    The explicit tenant filter is not redundant with RLS: a valid id from
    another tenant must 404, and we never rely on RLS alone.
    """
    row = (
        await db.execute(
            select(CostAnomaly).where(
                CostAnomaly.id == anomaly_id,
                CostAnomaly.tenant_id == user.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Anomaly not found")

    if row.acknowledged_at is None:
        row.acknowledged_at = datetime.now(UTC)
        row.acknowledged_by_user_id = user.user_id

        await log_audit_event(
            db,
            event_type="cost.anomaly.acknowledged",
            action="update",
            actor_id=user.user_id,
            actor_email=user.email,
            tenant_id=user.tenant_id,
            resource_type="cost_anomaly",
            resource_id=str(row.id),
            details={
                "provider": row.provider.value if row.provider else None,
                "usage_date": str(row.usage_date),
                "observed_usd": str(row.observed_usd),
                "baseline_usd": str(row.baseline_usd),
                "ratio": str(row.ratio),
            },
        )
        await db.commit()
        await db.refresh(row)

    return AnomalyPayload.model_validate(row)
