"""Promptly Guide router — `/api/v1/guide` (promptly-guide #37, #38).

Admin (Atlas JWT):
  PUT  /guide/connection           TenantAdmin  connect a Firebase project; choose kinds offered
  GET  /guide/connection           TenantAdmin
  GET  /guide/adoption/report      Analyst      figures for a month, through the gate
  GET  /guide/pilot-report         Analyst      the 30-day pilot report (#39), JSON or markdown

Guide (Firebase ID token as Bearer, no device row, no Atlas user):
  GET  /guide/offer                which kinds the organisation offers to count
  GET  /guide/approved-tools       the organisation's sanctioned AI tools, for steering (F14)
  GET  /guide/groups               the signed-in person's SCIM groups, by name (#58)

Admin, SCIM (#58):
  POST   /guide/scim-token         TenantAdmin  make (or replace) the tenant's SCIM token
  DELETE /guide/scim-token         TenantAdmin  revoke it
  POST /guide/adoption             one opted-in person's month: team, month, categories

A Guide Mac is never enrolled as a device here. `devices/register` records
`user_external_id` on every device, and a per-person row is what Guide's E4
promise rules out. The Firebase token is verified on each call to find the tenant
and is then dropped: its email, name and groups are not read, logged or stored.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
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
from app.auth.scim_token import generate_scim_token
from app.config import get_settings
from app.database import get_db_session, set_tenant_guc
from app.errors import AppException, ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.models.guide_adoption import ADOPTION_KINDS, GuideConnection
from app.models.guide_scim import GuideScimGroup, GuideScimMember, GuideScimToken, GuideScimUser
from app.models.tenant import Tenant
from app.models.use_case import UseCase, UseCaseStatus
from app.schemas.guide import (
    GuideAdoptionReportOut,
    GuideApprovedToolOut,
    GuideApprovedToolsOut,
    GuideConnectionIn,
    GuideConnectionOut,
    GuideContributionIn,
    GuideContributionOut,
    GuideFigureOut,
    GuideGroupsOut,
    GuideOfferOut,
    GuidePilotMonthOut,
    GuidePilotReportOut,
    GuideRiskOut,
    GuideRiskRowOut,
    GuideScimTokenOut,
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
    # Read only by `GET /guide/groups`; never stored.
    email: str | None = None


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
    return GuideCaller(
        tenant_id=tenant_id,
        offered=frozenset(offered),
        subject=identity.subject,
        email=identity.email,
    )


@router.get("/offer", response_model=GuideOfferOut)
async def offer(caller: GuideCaller = Depends(require_guide_caller)) -> GuideOfferOut:
    return GuideOfferOut(
        offered_kinds=_kinds(list(caller.offered)),  # type: ignore[arg-type]
        minimum_group_size=adoption.MINIMUM_GROUP_SIZE,
    )


# Atlas's data-class taxonomy (`UseCase.data_classes`) in the words Guide's steering
# and policy answers use (promptly-guide `DataClass`). A class with no counterpart is
# left out rather than guessed at: Guide then simply does not say the tool is approved
# for it.
GUIDE_DATA_CLASSES = {
    "customer_pii": "customer data",
    "employee_pii": "personal data",
    "vendor_pii": "personal data",
    "donor_pii": "personal data",
    "phi": "personal data",
    "proprietary_code": "source code",
    "strategy_docs": "confidential",
    "financial_data": "confidential",
    "public": "public",
}
MAX_APPROVED_TOOLS = 100


def approved_tools(use_cases: list[tuple[str, str | None]]) -> list[GuideApprovedToolOut]:
    """One entry per tool named by an ACTIVE use case (names compared without case),
    with the union of what its use cases are approved for. Pure."""
    names: dict[str, str] = {}
    classes: dict[str, set[str]] = {}
    for tool, raw in use_cases:
        name = (tool or "").strip()
        if not name:
            continue
        key = name.casefold()
        names.setdefault(key, name)
        try:
            listed = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError):
            listed = []
        mapped = {
            GUIDE_DATA_CLASSES[c] for c in listed if isinstance(c, str) and c in GUIDE_DATA_CLASSES
        }
        classes.setdefault(key, set()).update(mapped)
    out = [
        GuideApprovedToolOut(name=names[k], data_classes=sorted(classes[k])) for k in sorted(names)
    ]
    return out[:MAX_APPROVED_TOOLS]


@router.get("/approved-tools", response_model=GuideApprovedToolsOut)
async def guide_approved_tools(
    caller: GuideCaller = Depends(require_guide_caller),
    db: AsyncSession = Depends(get_db_session),
) -> GuideApprovedToolsOut:
    """The organisation's sanctioned AI tools, from its AI use-case registry: every tool
    an ACTIVE use case names. Organisation configuration only -- tool names and data
    classes, never who registered or owns a use case. Guide reads it to steer people
    from an unapproved AI tool to an approved one (promptly-guide #57, F14)."""
    rows = (
        await db.execute(
            select(UseCase.tool, UseCase.data_classes).where(
                UseCase.tenant_id == caller.tenant_id,
                UseCase.status == UseCaseStatus.ACTIVE,
            )
        )
    ).all()
    return GuideApprovedToolsOut(tools=approved_tools([(r.tool, r.data_classes) for r in rows]))


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


# Asking for a figure about one person is refused, and says why, rather than answered
# with an empty report that would read as "nobody" (promptly-guide #38, "When a customer
# asks for more"). There is no per-person data to answer it with in any case.
_PERSON_PARAMETERS = {
    "user",
    "user_id",
    "userid",
    "email",
    "person",
    "employee",
    "upn",
    "device",
    "device_id",
    "subject",
    "sub",
    "member",
}


def _refuse_a_person(request: Request) -> None:
    asked = {k.lower() for k in request.query_params} & _PERSON_PARAMETERS
    if asked:
        raise AppException(
            code="ADOPTION_IS_BY_TEAM",
            message=(
                "Promptly Guide's adoption figures are by team and month only, from teams of "
                f"{adoption.MINIMUM_GROUP_SIZE} or more. There is no figure about one person, "
                "and none is kept to be asked for."
            ),
            status_code=http_status.HTTP_400_BAD_REQUEST,
            details={"refused_parameters": sorted(asked)},
        )


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
    request: Request,
    user: Analyst,
    period: str = Query(..., description="YYYY-MM"),
    db: AsyncSession = Depends(get_tenant_db_session),
) -> GuideAdoptionReportOut:
    _refuse_a_person(request)
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
    request: Request,
    user: Analyst,
    format: Literal["json", "markdown"] = Query("json"),  # noqa: A002 — the query name
    db: AsyncSession = Depends(get_tenant_db_session),
) -> GuidePilotReportOut | PlainTextResponse:
    """The 30-day pilot report (promptly-guide #39): AI tools in use and where people
    get stuck from Guide's gated figures, risky behaviour from Atlas's own prompt
    telemetry. Aggregate only; see `app/services/guide_pilot_report.py`."""
    _refuse_a_person(request)
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


# ---------------------------------------------------------------------------
# SCIM (#58): the token the identity provider uses, and a person's groups
# ---------------------------------------------------------------------------


@router.get("/groups", response_model=GuideGroupsOut)
async def guide_groups(
    caller: GuideCaller = Depends(require_guide_caller),
    db: AsyncSession = Depends(get_db_session),
) -> GuideGroupsOut:
    """The SCIM groups the signed-in person is in, by name, for group-based enablement:
    which of the organisation's documents Guide answers from. Found by the sign-in's
    email against the provisioned userName; none when the person is not provisioned,
    or is deprovisioned (`active` false)."""
    if not caller.email:
        return GuideGroupsOut(groups=[])
    rows = (
        await db.execute(
            select(GuideScimGroup.display_name)
            .join(GuideScimMember, GuideScimMember.group_id == GuideScimGroup.id)
            .join(GuideScimUser, GuideScimUser.id == GuideScimMember.user_id)
            .where(
                GuideScimUser.tenant_id == caller.tenant_id,
                GuideScimUser.user_name_key == caller.email.lower(),
                GuideScimUser.active.is_(True),
            )
        )
    ).scalars()
    return GuideGroupsOut(groups=sorted(set(rows)))


@router.post("/scim-token", response_model=GuideScimTokenOut)
async def make_scim_token(
    user: TenantAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> GuideScimTokenOut:
    """A new SCIM token for the tenant's identity provider, shown once. Replaces any
    token the tenant had: the old one stops working at once."""
    tenant_id = _tenant_of(user.tenant_id)
    raw, token_hash = generate_scim_token()
    row = (
        await db.execute(select(GuideScimToken).where(GuideScimToken.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is None:
        db.add(GuideScimToken(tenant_id=tenant_id, token_hash=token_hash))
    else:
        row.token_hash = token_hash
    await db.commit()
    return GuideScimTokenOut(token=raw, endpoint_path="/api/v1/scim/v2")


@router.delete("/scim-token", status_code=http_status.HTTP_204_NO_CONTENT)
async def revoke_scim_token(
    user: TenantAdmin,
    db: AsyncSession = Depends(get_tenant_db_session),
) -> None:
    tenant_id = _tenant_of(user.tenant_id)
    row = (
        await db.execute(select(GuideScimToken).where(GuideScimToken.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if row is not None:
        await db.delete(row)
        await db.commit()
