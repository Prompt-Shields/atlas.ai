"""Prompt telemetry router — `/api/v1/telemetry`.

Ingestion (X-API-Key, used by the prompt-shields clients):
  POST /telemetry/prompt-events       batch insert, per-row skip-with-reason

Dashboard aggregates (JWT-authenticated, tenant-scoped):
  GET /telemetry/prompt-activity/summary
  GET /telemetry/prompt-activity/timeseries
  GET /telemetry/prompt-activity/breakdown?by=app|pii_category|source

Contract: docs/superpowers/specs/2026-06-11-prompt-telemetry-design.md.
Sister surface to app/routers/extension.py (same auth model).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    get_tenant_db_session,
    require_api_key,
)
from app.database import get_db_session, set_tenant_guc
from app.errors import UnauthorizedError
from app.models.prompt_event import PromptEvent
from app.models.user import APIKey
from app.schemas.telemetry import (
    PromptActivityBreakdown,
    PromptActivityBreakdownRow,
    PromptActivityBucket,
    PromptActivitySummary,
    PromptActivityTimeseries,
    PromptEventIn,
    PromptEventIngestResponse,
    PromptEventKind,
    PromptEventSource,
)

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


class PromptEventBatch(BaseModel):
    # Rows are deliberately untyped here: per-row validation with
    # skip-with-reason happens in the handler. Typing this as
    # list[dict[str, Any]] would 422 on a non-dict row (e.g. "garbage" or null)
    # before the loop even runs; list[Any] lets non-dict values fall into the
    # existing per-row skip path via PromptEventIn.model_validate's ValidationError.
    events: list[Any] = Field(..., min_length=1, max_length=500)


def _as_utc(dt: datetime) -> datetime:
    """Clients may send naive ISO timestamps; treat them as UTC."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@router.post("/prompt-events", response_model=PromptEventIngestResponse)
async def ingest_prompt_events(
    payload: PromptEventBatch,
    api_key_record: Annotated[APIKey, Depends(require_api_key)],
    db: AsyncSession = Depends(get_db_session),
) -> PromptEventIngestResponse:
    if api_key_record.tenant_id is None:
        raise UnauthorizedError("Prompt telemetry requires a tenant-scoped API key.")

    # RLS: API-key sessions don't go through get_tenant_db_session, so the
    # tenant GUC is never set on this connection. Works today only because
    # the app role owns the table (owners bypass RLS); set it explicitly so
    # the insert path survives a move to a non-owner role.
    await set_tenant_guc(db, api_key_record.tenant_id)

    ingested = 0
    skipped = 0
    reasons: list[str] = []

    for idx, raw in enumerate(payload.events):
        try:
            event = PromptEventIn.model_validate(raw)
        except ValidationError as exc:
            skipped += 1
            # Deliberately use only loc/msg — the error dict's "input" key would
            # echo rejected content (e.g. prompt text) into responses/logs.
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first["loc"]) or "event"
            reasons.append(f"events[{idx}]: {loc}: {first['msg']}")
            continue

        occurred = (
            _as_utc(event.occurred_at) if event.occurred_at is not None else datetime.now(UTC)
        )
        db.add(
            PromptEvent(
                tenant_id=api_key_record.tenant_id,
                source=event.source,
                event_kind=event.event_kind,
                app_id=event.app_id,
                prompt_hash=event.prompt_hash,
                action=event.action,
                severity=event.severity,
                pii_categories=event.pii_categories,
                device_fingerprint=event.device_fingerprint,
                user_external_id=event.user_external_id,
                session_id=event.session_id,
                vendor=event.vendor,
                model=event.model,
                tokens_in=event.tokens_in,
                tokens_out=event.tokens_out,
                estimated_cost_usd=event.estimated_cost_usd,
                occurrences=event.occurrences,
                occurred_at=occurred,
                api_key_id=api_key_record.id,
            )
        )
        ingested += 1

    await db.commit()
    return PromptEventIngestResponse(ingested=ingested, skipped=skipped, skipped_reasons=reasons)


# ── Shared filter helper ────────────────────────────────────────────────────────


def _build_base_filter(
    user: CurrentUser,
    since: datetime | None,
    until: datetime | None,
    source: PromptEventSource | None,
    app_id: str | None,
):
    """Return a list of SQLAlchemy WHERE clauses for aggregate queries.

    Explicit tenant_id filter is always included regardless of RLS — this
    is defence-in-depth for the SQLite test suite which has no RLS at all.

    Super-admin note: get_tenant_db_session EXEMPTS super-admins (it returns
    the session without setting the tenant GUC).  A super-admin whose
    CurrentUser has tenant_id=None will reach here, and
    `PromptEvent.tenant_id == None` compiles to IS NULL, returning an empty
    result set.  This is deliberate — these endpoints are tenant-scoped
    dashboards; a cross-tenant view would be a separate feature.
    """
    filters = [PromptEvent.tenant_id == user.tenant_id]
    if since is not None:
        since_utc = since if since.tzinfo is not None else since.replace(tzinfo=UTC)
        filters.append(PromptEvent.occurred_at >= since_utc)
    if until is not None:
        until_utc = until if until.tzinfo is not None else until.replace(tzinfo=UTC)
        # `until` is inclusive (<=), matching the spec's closed-interval window.
        filters.append(PromptEvent.occurred_at <= until_utc)
    if source is not None:
        filters.append(PromptEvent.source == source)
    if app_id is not None:
        filters.append(PromptEvent.app_id == app_id)
    return filters


def _violations_sum():
    """Reusable SQLAlchemy expression: SUM(occurrences) for VIOLATION rows, else 0.

    Extracted to avoid copy-pasting the case() expression across summary,
    timeseries, and both breakdown branches.
    """
    return func.sum(
        case(
            (PromptEvent.event_kind == PromptEventKind.VIOLATION, PromptEvent.occurrences),
            else_=0,
        )
    )


# ── GET /prompt-activity/summary ───────────────────────────────────────────────


@router.get("/prompt-activity/summary", response_model=PromptActivitySummary)
async def get_prompt_activity_summary(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_tenant_db_session),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    source: PromptEventSource | None = Query(None),
    app_id: str | None = Query(None),
) -> PromptActivitySummary:
    """Aggregate summary: total prompts, violations, violation rate, devices, sources.

    Auth: get_current_user (all tenant roles may read).  These aggregates expose
    only counts — no prompt hashes, no user identifiers — so a TenantAdmin gate
    (as used in monitoring.py) is not required.  The deviation is deliberate.
    """
    filters = _build_base_filter(user, since, until, source, app_id)

    stmt = select(
        func.coalesce(func.sum(PromptEvent.occurrences), 0).label("total_prompts"),
        func.coalesce(_violations_sum(), 0).label("total_violations"),
        func.count(func.distinct(PromptEvent.device_fingerprint)).label("active_devices"),
        func.count(func.distinct(PromptEvent.source)).label("active_sources"),
    ).where(*filters)

    result = await db.execute(stmt)
    row = result.one()

    total_prompts = int(row.total_prompts)
    total_violations = int(row.total_violations)

    # active_devices COUNT(DISTINCT ...) counts NULLs as 0 in SQL — correct.
    active_devices = int(row.active_devices)
    active_sources = int(row.active_sources)

    violation_rate = total_violations / total_prompts if total_prompts > 0 else 0.0

    return PromptActivitySummary(
        total_prompts=total_prompts,
        total_violations=total_violations,
        violation_rate=violation_rate,
        active_devices=active_devices,
        active_sources=active_sources,
    )


# ── GET /prompt-activity/timeseries ────────────────────────────────────────────


@router.get("/prompt-activity/timeseries", response_model=PromptActivityTimeseries)
async def get_prompt_activity_timeseries(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_tenant_db_session),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    source: PromptEventSource | None = Query(None),
    app_id: str | None = Query(None),
) -> PromptActivityTimeseries:
    """Daily timeseries buckets (no empty-day fabrication).

    Uses func.date() which works on both SQLite and Postgres for grouping.
    Dates are returned as YYYY-MM-DD strings.
    """
    filters = _build_base_filter(user, since, until, source, app_id)

    # Under Postgres, date(timestamptz) casts in the *session* timezone; this
    # code assumes UTC sessions (the deployment default).  If a non-UTC
    # deployment ever appears, harden with func.timezone('UTC', col) before
    # the date() cast.
    day_expr = func.date(PromptEvent.occurred_at).label("day")

    stmt = (
        select(
            day_expr,
            func.sum(PromptEvent.occurrences).label("prompts"),
            _violations_sum().label("violations"),
        )
        .where(*filters)
        .group_by(day_expr)
        .order_by(day_expr.asc())
    )

    result = await db.execute(stmt)
    rows = result.all()

    buckets = [
        PromptActivityBucket(
            date=str(row.day),  # SQLite returns str; Postgres returns date object
            prompts=int(row.prompts),
            violations=int(row.violations),
        )
        for row in rows
    ]
    return PromptActivityTimeseries(buckets=buckets)


# ── GET /prompt-activity/breakdown ─────────────────────────────────────────────


@router.get("/prompt-activity/breakdown", response_model=PromptActivityBreakdown)
async def get_prompt_activity_breakdown(
    user: Annotated[CurrentUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_tenant_db_session),
    by: Literal["app", "pii_category", "source"] = Query(...),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    source: PromptEventSource | None = Query(None),
    app_id: str | None = Query(None),
) -> PromptActivityBreakdown:
    """Prompt activity breakdown grouped by app, pii_category, or source.

    by=app:
        GROUP BY app_id; NULL app_id → key "(none)"; sorted by prompts desc.
    by=source:
        GROUP BY source (key = enum wire value string); sorted by prompts desc.
    by=pii_category:
        Computed in Python for SQLite/Postgres portability: iterate over
        VIOLATION rows' pii_categories dicts, weight each category count by
        row.occurrences.  `violations` holds the weighted count; `prompts`
        mirrors it because pii categories only exist on violations.
        Sort by violations descending.

        Postgres upgrade path: when volume demands, replace the Python fan-out
        with jsonb_each_text(pii_categories) and push the SUM into SQL.
    """
    filters = _build_base_filter(user, since, until, source, app_id)

    if by in ("app", "source"):
        # Both app and source breakdowns share the same SQL shape — only the
        # group-key expression and the key renderer differ.
        if by == "app":
            group_key_expr = func.coalesce(PromptEvent.app_id, "(none)").label("key")
            key_render = str
        else:  # source
            group_key_expr = PromptEvent.source.label("key")
            # source is an enum column; .value gives the wire string
            key_render = lambda v: v.value if hasattr(v, "value") else str(v)  # noqa: E731

        stmt = (
            select(
                group_key_expr,
                func.sum(PromptEvent.occurrences).label("prompts"),
                _violations_sum().label("violations"),
            )
            .where(*filters)
            .group_by(group_key_expr)
            .order_by(func.sum(PromptEvent.occurrences).desc())
        )
        result = await db.execute(stmt)
        rows = [
            PromptActivityBreakdownRow(
                key=key_render(row.key),
                prompts=int(row.prompts),
                violations=int(row.violations),
            )
            for row in result
        ]

    else:  # by == "pii_category"
        # Computed in Python for SQLite/Postgres portability: iterate over
        # VIOLATION rows' pii_categories dicts, weight each category count by
        # row.occurrences.  `violations` holds the weighted count; `prompts`
        # mirrors it because pii categories only exist on violations.
        #
        # Postgres upgrade path: when volume demands, replace this Python
        # fan-out with jsonb_each_text(pii_categories) and push the SUM into SQL.
        violation_filter = [*filters, PromptEvent.event_kind == PromptEventKind.VIOLATION]
        stmt = select(PromptEvent.pii_categories, PromptEvent.occurrences).where(*violation_filter)
        result = await db.execute(stmt)
        category_totals: dict[str, int] = {}
        for row in result:
            cats: dict = row.pii_categories or {}
            occ: int = row.occurrences or 0
            for cat, count in cats.items():
                category_totals[cat] = category_totals.get(cat, 0) + count * occ

        # pii categories only exist on violations; prompts == violations for this view
        rows = sorted(
            [
                PromptActivityBreakdownRow(key=k, prompts=v, violations=v)
                for k, v in category_totals.items()
            ],
            key=lambda r: r.violations,
            reverse=True,
        )

    return PromptActivityBreakdown(by=by, rows=rows)
