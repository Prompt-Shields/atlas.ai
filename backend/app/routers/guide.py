"""Promptly Guide router — `/api/v1/guide` (promptly-guide #37, #38).

Admin (Atlas JWT):
  PUT  /guide/connection           TenantAdmin  connect a Firebase project; choose kinds offered
  GET  /guide/connection           TenantAdmin
  GET  /guide/adoption/report      Analyst      figures for a month, through the gate
  GET  /guide/pilot-report         Analyst      the 30-day pilot report (#39), JSON or markdown

Guide (Firebase ID token as Bearer, no device row, no Atlas user):
  GET  /guide/offer                which kinds the organisation offers to count
  POST /guide/adoption             one opted-in person's month: team, month, categories

A Guide Mac is never enrolled as a device here. `devices/register` records
`user_external_id` on every device, and a per-person row is what Guide's E4
promise rules out. The Firebase token is verified on each call to find the tenant
and is then dropped: its email, name and groups are not read, logged or stored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from fastapi.responses import PlainTextResponse
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import (
    Analyst,
    TenantAdmin,
    bearer_scheme,
    get_tenant_db_session,
)
from app.auth.firebase_token import (
    FirebaseTokenError,
    FirebaseTokenVerifier,
    get_firebase_verifier,
    unverified_target,
)
from app.config import get_settings
from app.database import get_db_session, set_tenant_guc
from app.errors import AppException, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.models.guide_adoption import ADOPTION_KINDS, GuideConnection
from app.models.tenant import Tenant
from app.schemas.guide import (
    GuideAdoptionReportOut,
    GuideConnectionIn,
    GuideConnectionOut,
    GuideContributionIn,
    GuideContributionOut,
    GuideFigureOut,
    GuideOfferOut,
    GuidePilotMonthOut,
    GuidePilotReportOut,
    GuideRiskOut,
    GuideRiskRowOut,
)
from app.services import guide_adoption_service as adoption
from app.services import guide_pilot_report as pilot

router = APIRouter(prefix="/guide", tags=["Promptly Guide"])

OPT_IN_NOTE = (
    "Only people who chose to be counted are in these figures, so a range is a share "
    "of them, not of the whole team."
)


def _kinds(values: object) -> list[str]:
    """Stored kinds, known ones only, in a fixed order."""
    held = set(values) if isinstance(values, list) else set()
    return [k for k in ADOPTION_KINDS if k in held]


# ---------------------------------------------------------------------------
# Guide's caller: a Firebase ID token, resolved to a tenant and nothing more
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuideCaller:
    tenant_id: uuid.UUID
    offered: frozenset[str]
    # Firebase `sub`. Used once, for the monthly receipt; never stored as is.
    subject: str


async def _connection_for(
    db: AsyncSession, project_id: str, firebase_tenant: str
) -> tuple[uuid.UUID, list[str]] | None:
    # Pre-tenant lookup, the way `require_device_token` does it: on Postgres a
    # SECURITY DEFINER helper reads past RLS for this one keyed lookup; the sqlite
    # test schema has no RLS and no helper, so it reads the table directly.
    if db.get_bind().dialect.name == "postgresql":
        row = (
            await db.execute(
                text(
                    "SELECT tenant_id, offered_kinds "
                    "FROM grc.resolve_guide_connection(:project, :tenant)"
                ),
                {"project": project_id, "tenant": firebase_tenant},
            )
        ).one_or_none()
        return None if row is None else (row.tenant_id, _kinds(row.offered_kinds))
    found = (
        await db.execute(
            select(GuideConnection).where(
                GuideConnection.firebase_project_id == project_id,
                GuideConnection.firebase_tenant_id == firebase_tenant,
            )
        )
    ).scalar_one_or_none()
    return None if found is None else (found.tenant_id, _kinds(found.offered_kinds))


async def require_guide_caller(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db_session),
    verifier: FirebaseTokenVerifier = Depends(get_firebase_verifier),
) -> GuideCaller:
    if credentials is None:
        raise UnauthorizedError("Bearer token required")
    token = credentials.credentials
    try:
        project_id, firebase_tenant = unverified_target(token)
    except FirebaseTokenError:
        raise UnauthorizedError("Invalid or expired token")
    connection = await _connection_for(db, project_id, firebase_tenant)
    if connection is None:
        # Same answer as a bad token: which projects are connected is not
        # something to learn by asking.
        raise UnauthorizedError("Invalid or expired token")
    try:
        identity = await verifier.verify(token, project_id)
    except FirebaseTokenError:
        raise UnauthorizedError("Invalid or expired token")
    if identity.firebase_tenant != firebase_tenant:
        raise UnauthorizedError("Invalid or expired token")
    tenant_id, offered = connection
    await set_tenant_guc(db, tenant_id)
    return GuideCaller(tenant_id=tenant_id, offered=frozenset(offered), subject=identity.subject)


@router.get("/offer", response_model=GuideOfferOut)
async def offer(caller: GuideCaller = Depends(require_guide_caller)) -> GuideOfferOut:
    return GuideOfferOut(
        offered_kinds=_kinds(list(caller.offered)),  # type: ignore[arg-type]
        minimum_group_size=adoption.MINIMUM_GROUP_SIZE,
    )


@router.post("/adoption", response_model=GuideContributionOut)
async def contribute(
    payload: GuideContributionIn,
    caller: GuideCaller = Depends(require_guide_caller),
    db: AsyncSession = Depends(get_db_session),
) -> GuideContributionOut:
    try:
        contribution = adoption.validate_contribution(
            team=payload.team,
            period=adoption.period_text(payload.period.year, payload.period.month),
            categories=[(r.kind, r.id) for r in payload.reached],
            offered=set(caller.offered),
        )
    except adoption.ContributionRejected as error:
        raise AppException(
            code="CONTRIBUTION_REJECTED",
            message=str(error),
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    receipt = adoption.receipt(
        get_settings().jwt_secret_key, caller.tenant_id, caller.subject, contribution.period
    )
    counted = await adoption.record_contribution(db, caller.tenant_id, contribution, receipt)
    await db.commit()
    # Already counted this month is not an error: Guide may retry after a lost
    # response, and the answer it needs is "nothing more to send".
    return GuideContributionOut(counted=counted)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def _tenant_of(user_tenant: uuid.UUID | None) -> uuid.UUID:
    if user_tenant is None:
        raise ForbiddenError("Tenant context required")
    return user_tenant


@router.put("/connection", response_model=GuideConnectionOut)
async def put_connection(
    payload: GuideConnectionIn,
    user: TenantAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> GuideConnectionOut:
    tenant_id = _tenant_of(user.tenant_id)
    row = (
        await db.execute(select(GuideConnection).where(GuideConnection.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is None:
        row = GuideConnection(tenant_id=tenant_id)
        db.add(row)
    row.firebase_project_id = payload.firebase_project_id.strip()
    row.firebase_tenant_id = payload.firebase_tenant_id.strip()
    row.offered_kinds = _kinds(list(payload.offered_kinds))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ConflictError("That Firebase project and tenant are already connected")
    return GuideConnectionOut(
        firebase_project_id=row.firebase_project_id,
        firebase_tenant_id=row.firebase_tenant_id,
        offered_kinds=_kinds(row.offered_kinds),  # type: ignore[arg-type]
    )


@router.get("/connection", response_model=GuideConnectionOut)
async def get_connection(
    user: TenantAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> GuideConnectionOut:
    tenant_id = _tenant_of(user.tenant_id)
    row = (
        await db.execute(select(GuideConnection).where(GuideConnection.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Guide connection")
    return GuideConnectionOut(
        firebase_project_id=row.firebase_project_id,
        firebase_tenant_id=row.firebase_tenant_id,
        offered_kinds=_kinds(row.offered_kinds),  # type: ignore[arg-type]
    )


@router.get("/adoption/report", response_model=GuideAdoptionReportOut)
async def adoption_report(
    user: Analyst,
    period: str = Query(..., description="YYYY-MM"),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> GuideAdoptionReportOut:
    tenant_id = _tenant_of(user.tenant_id)
    try:
        year, month = adoption.parse_period(period)
    except adoption.ContributionRejected as error:
        raise AppException(
            code="BAD_PERIOD",
            message=str(error),
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    month_text = adoption.period_text(year, month)
    row = (
        await db.execute(select(GuideConnection).where(GuideConnection.tenant_id == tenant_id))
    ).scalar_one_or_none()
    offered = set(_kinds(row.offered_kinds)) if row is not None else set()
    tallies = await adoption.tallies_for(db, tenant_id, month_text)
    report = adoption.gate(tallies, month_text, offered)
    return GuideAdoptionReportOut(
        period=report.period,
        figures=[
            GuideFigureOut(
                team=f.team,
                category_kind=f.category_kind,
                category_id=f.category_id,
                band=f.band.text,
                band_lower=f.band.lower,
            )
            for f in report.figures
        ],
        teams_too_small=report.teams_too_small,
        suppressed_categories=report.suppressed_categories,
        minimum_group_size=adoption.MINIMUM_GROUP_SIZE,
        note=OPT_IN_NOTE,
    )


def _figure_out(f: adoption.Figure) -> GuideFigureOut:
    return GuideFigureOut(
        team=f.team,
        category_kind=f.category_kind,
        category_id=f.category_id,
        band=f.band.text,
        band_lower=f.band.lower,
    )


@router.get("/pilot-report", response_model=None)
async def pilot_report(
    user: Analyst,
    format: Literal["json", "markdown"] = Query("json"),  # noqa: A002 — the query name
    db: AsyncSession = Depends(get_tenant_db_session),
) -> GuidePilotReportOut | PlainTextResponse:
    """The 30-day pilot report (promptly-guide #39): AI tools in use and where people
    get stuck from Guide's gated figures, risky behaviour from Atlas's own prompt
    telemetry. Aggregate only; see `app/services/guide_pilot_report.py`."""
    tenant_id = _tenant_of(user.tenant_id)
    try:
        report = await pilot.build(db, tenant_id)
    except pilot.PilotNotReady as not_ready:
        raise AppException(
            code="PILOT_NOT_READY",
            message=str(not_ready),
            status_code=http_status.HTTP_409_CONFLICT,
            details={"ready_on": not_ready.ready_on.date().isoformat()},
        )
    if report is None:
        raise NotFoundError("Guide connection")
    if format == "markdown":
        tenant = await db.get(Tenant, tenant_id)
        name = tenant.name if tenant is not None else "your organisation"
        return PlainTextResponse(pilot.markdown(report, name), media_type="text/markdown")
    risk = report.risk
    return GuidePilotReportOut(
        connected_at=report.connected_at.isoformat(),
        generated_at=report.generated_at.isoformat(),
        offered_kinds=report.offered_kinds,
        months=[
            GuidePilotMonthOut(
                period=m.period,
                tools=[_figure_out(f) for f in m.tools],
                finished=[_figure_out(f) for f in m.finished],
                not_finished=[_figure_out(f) for f in m.not_finished],
                topics=[_figure_out(f) for f in m.topics],
                teams_too_small=m.teams_too_small,
                suppressed_categories=m.suppressed_categories,
            )
            for m in report.months
        ],
        risk=GuideRiskOut(
            since=risk.since.isoformat(),
            until=risk.until.isoformat(),
            by_category=[
                GuideRiskRowOut(key=r.key, events=r.events, devices=r.devices)
                for r in risk.by_category
            ],
            by_app=[
                GuideRiskRowOut(key=r.key, events=r.events, devices=r.devices) for r in risk.by_app
            ],
            by_action=[
                GuideRiskRowOut(key=r.key, events=r.events, devices=r.devices)
                for r in risk.by_action
            ],
            suppressed=risk.suppressed,
            minimum_devices=pilot.MINIMUM_DEVICES,
        ),
        minimum_group_size=adoption.MINIMUM_GROUP_SIZE,
        notes=report.notes,
    )
