"""Device risk engine — a Phase-4 directive producer. Aggregates high/critical
prompt-violation activity per user over a window and emits coaching nudges to
that user's active devices, with a per-device cooldown so a repeat scan doesn't
re-nudge. Links devices by user_external_id (widget registers fingerprint=nil
today)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enrolled_device import EnrolledDevice
from app.models.prompt_event import PromptEvent
from app.schemas.directive import CoachingTag
from app.schemas.telemetry import PromptEventKind, PromptEventSeverity
from app.services.device_directive_sse import (
    device_directive_notifier,
    directive_available_event,
)
from app.services.directive_emit import emit_nudge_directive, recent_directive_exists

_RISK_ORIGIN = "risk_engine"

# Mirrors the NudgeCreateIn caps in app/schemas/directive.py (spec §5 — even
# generated text is display data on-device, so it stays bounded here too).
_TITLE_CAP = 80
_BODY_CAP = 280
# app_id is String(120); keep a single app name from eating the body budget.
_APP_CAP = 40


@dataclass(frozen=True)
class RiskEngineConfig:
    min_violations: int = 5
    window_hours: int = 24
    cooldown_hours: int = 24


@dataclass(frozen=True)
class UserRisk:
    """One at-risk user's aggregate for the window, used to compose the nudge."""

    user_external_id: str
    violation_count: int
    has_critical: bool
    top_app: str | None


_RISKY_SEVERITIES = (PromptEventSeverity.HIGH, PromptEventSeverity.CRITICAL)


def _window_start(config: RiskEngineConfig) -> datetime:
    return datetime.now(UTC) - timedelta(hours=config.window_hours)


async def high_risk_user_ids(
    session: AsyncSession, tenant_id: uuid.UUID, config: RiskEngineConfig
) -> list[str]:
    since = _window_start(config)
    stmt = (
        select(PromptEvent.user_external_id, func.sum(PromptEvent.occurrences).label("total"))
        .where(
            PromptEvent.tenant_id == tenant_id,
            PromptEvent.event_kind == PromptEventKind.VIOLATION,
            PromptEvent.severity.in_(_RISKY_SEVERITIES),
            PromptEvent.user_external_id.is_not(None),
            PromptEvent.occurred_at >= since,
        )
        .group_by(PromptEvent.user_external_id)
        .having(func.sum(PromptEvent.occurrences) >= config.min_violations)
    )
    rows = (await session.execute(stmt)).all()
    return [r.user_external_id for r in rows]


async def high_risk_users(
    session: AsyncSession, tenant_id: uuid.UUID, config: RiskEngineConfig
) -> list[UserRisk]:
    """Qualifying users plus the detail the nudge text needs.

    Two queries on purpose: the threshold stays a DB-side HAVING (same
    semantics as high_risk_user_ids), then one grouped detail query runs only
    for the users that already qualified.
    """
    user_ids = await high_risk_user_ids(session, tenant_id, config)
    if not user_ids:
        return []

    critical_flag = case((PromptEvent.severity == PromptEventSeverity.CRITICAL, 1), else_=0)
    stmt = (
        select(
            PromptEvent.user_external_id,
            PromptEvent.app_id,
            func.sum(PromptEvent.occurrences).label("total"),
            func.max(critical_flag).label("any_critical"),
        )
        .where(
            PromptEvent.tenant_id == tenant_id,
            PromptEvent.event_kind == PromptEventKind.VIOLATION,
            PromptEvent.severity.in_(_RISKY_SEVERITIES),
            PromptEvent.user_external_id.in_(user_ids),
            PromptEvent.occurred_at >= _window_start(config),
        )
        .group_by(PromptEvent.user_external_id, PromptEvent.app_id)
    )
    rows = (await session.execute(stmt)).all()

    totals: dict[str, int] = {}
    critical: dict[str, bool] = {}
    best_app: dict[str, tuple[int, str | None]] = {}
    for row in rows:
        user = row.user_external_id
        count = int(row.total or 0)
        totals[user] = totals.get(user, 0) + count
        critical[user] = critical.get(user, False) or bool(row.any_critical)
        # Attribute the nudge to whichever app drove the most violations; ties
        # resolve to the first row seen, which is arbitrary but stable enough.
        if row.app_id and count > best_app.get(user, (0, None))[0]:
            best_app[user] = (count, row.app_id)

    return [
        UserRisk(
            user_external_id=user,
            violation_count=totals.get(user, 0),
            has_critical=critical.get(user, False),
            top_app=best_app.get(user, (0, None))[1],
        )
        for user in user_ids
    ]


def _risk_nudge_payload(risk: UserRisk, config: RiskEngineConfig) -> dict:
    """Compose nudge text from what actually fired, within the §5 length caps."""
    if risk.has_critical:
        title = "Critical prompt activity detected"
        lead = "critical-severity"
    else:
        title = "High-risk prompt activity detected"
        lead = "high-severity"

    where = ""
    if risk.top_app:
        app = risk.top_app[:_APP_CAP]
        where = f", mostly in {app},"

    plural = "s" if risk.violation_count != 1 else ""
    body = (
        f"{risk.violation_count} {lead} policy violation{plural}{where} "
        f"in the last {config.window_hours}h. Review the guidance before continuing."
    )

    return {
        "title": title[:_TITLE_CAP],
        "body": body[:_BODY_CAP],
        "severity": "high",
        "coaching_tag": CoachingTag.policy_violation.value,
    }


async def evaluate_tenant_risk(
    session: AsyncSession, tenant_id: uuid.UUID, config: RiskEngineConfig
) -> int:
    """Evaluate one tenant, emit risk nudges to at-risk users' active devices.
    Returns count emitted. Idempotent within the cooldown. Caller commits."""
    risks = await high_risk_users(session, tenant_id, config)
    if not risks:
        return 0
    by_user = {r.user_external_id: r for r in risks}

    devices = (
        (
            await session.execute(
                select(EnrolledDevice).where(
                    EnrolledDevice.tenant_id == tenant_id,
                    EnrolledDevice.user_external_id.in_(list(by_user)),
                    EnrolledDevice.enrollment_state == "active",
                )
            )
        )
        .scalars()
        .all()
    )

    emitted = 0
    cooldown = timedelta(hours=config.cooldown_hours)
    for device in devices:
        risk = by_user.get(device.user_external_id)
        if risk is None:
            continue
        if await recent_directive_exists(
            session, device_id=device.id, origin=_RISK_ORIGIN, within=cooldown
        ):
            continue
        await emit_nudge_directive(
            session,
            tenant_id=tenant_id,
            device_id=device.id,
            payload=_risk_nudge_payload(risk, config),
            origin=_RISK_ORIGIN,
            created_by=None,
        )
        emitted += 1
        try:
            await device_directive_notifier.publish(device.id, directive_available_event())
        except Exception:
            pass
    return emitted


async def run_for_tenant(
    session: AsyncSession, tenant_id: uuid.UUID, config: RiskEngineConfig | None = None
) -> int:
    """Thin driver: evaluate one tenant with defaults if no config given."""
    return await evaluate_tenant_risk(session, tenant_id, config or RiskEngineConfig())


@dataclass(frozen=True)
class SweepResult:
    """Outcome of one all-tenant sweep. `tenants_swept` counts only tenants
    that completed, so `tenants_swept + len(failed_tenant_ids)` is the input
    size."""

    tenants_swept: int = 0
    emitted: int = 0
    failed_tenant_ids: list[uuid.UUID] = field(default_factory=list)


SessionForTenant = Callable[[uuid.UUID], AbstractAsyncContextManager[AsyncSession]]


async def sweep_tenants(
    tenant_ids: Iterable[uuid.UUID],
    session_for_tenant: SessionForTenant,
    config: RiskEngineConfig | None = None,
    on_error: Callable[[uuid.UUID, Exception], None] | None = None,
) -> SweepResult:
    """Evaluate every tenant in turn, emitting risk nudges.

    One tenant's failure never aborts the sweep (same contract as the PEP
    watchdog in `routers/pep.py`): the tenant is recorded in
    `failed_tenant_ids`, `on_error` is invoked for logging, and the next
    tenant runs. Opening the scoped session is inside the guarded block on
    purpose — a failed `SET LOCAL app.current_tenant_id` is itself a
    per-tenant failure, not a reason to abandon the run.

    The session factory is injected rather than imported so this stays in the
    backend service layer: the worker passes `tenant_scoped_session`, which
    lives in the worker package.
    """
    config = config or RiskEngineConfig()
    swept = 0
    emitted = 0
    failed: list[uuid.UUID] = []

    for tenant_id in tenant_ids:
        try:
            async with session_for_tenant(tenant_id) as session:
                emitted += await evaluate_tenant_risk(session, tenant_id, config)
        except Exception as exc:  # noqa: BLE001 — one bad tenant must not abort the sweep.
            failed.append(tenant_id)
            if on_error is not None:
                on_error(tenant_id, exc)
            continue
        swept += 1

    return SweepResult(tenants_swept=swept, emitted=emitted, failed_tenant_ids=failed)
