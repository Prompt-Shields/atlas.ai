"""The Work client's guided-step meter and pool cap (promptly-guide #62).

promptly-guide `docs/step-metering.md` proposes, and this follows:

  * **Only hosted steps count** (its decision 1). A step answered on the Mac or
    by the customer's own endpoint never reaches Promptly, so nothing here
    sees it. A step is one call to Promptly's hosted model that returned an
    answer for one screen; a failed or refused call is not one.
  * **The Work client's meter lives in Atlas** (decision 2, option C). The
    hosted step gateway, which does not exist yet, calls `check()` before it
    serves a step and `record()` after. Guide itself never reports usage.
  * **The pool is per organisation per month**: the plan's steps per user
    (`guided_steps.enterprise_pool`) times the Work client's provisioned users.
    One number per month is kept, never who asked (E4).
  * **A hard cap is the administrator's choice, off by default** (decision 4).
    Off, steps above the pool are served and are overage; on, hosted steps
    stop at the pool until the month turns.
  * **Organisations with their own endpoint are not served hosted steps**:
    "it is used only when the administrator has not set the organisation's
    own endpoint", and their steps are never metered.

Posting the month's overage to the Stripe `guided_step` meter through the
billing outbox is the next slice; `PoolStatus.overage` is the figure it posts.

Months are UTC calendar months. `now` and `served_at` MUST come from the DB
server's clock at the call site, as for `entitlements.hard_locked()`.
"""

from __future__ import annotations

import enum
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.guided_step import GuidedStepCount, GuidedStepReceipt
from app.models.tenant import Tenant
from app.services.entitlements import Module, has_module, work_client_seats
from app.services.guided_steps import GuideTier, decide, enterprise_pool

# The gateway's id for one report: one served step, or a batch of them. Opaque
# to Atlas; kept only to count a retried report once.
_REPORT_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
# A guard against a malformed report, not a figure from the plan.
MAX_STEPS_PER_REPORT = 1000


class Refusal(str, enum.Enum):
    NO_WORK_CLIENT = "no_work_client"  # the tenant has no Work client to serve
    OWN_ENDPOINT = "own_endpoint"  # Guide answers from the organisation's own model
    POOL_USED = "pool_used"  # hard cap on, and this month's pool is used


class ReportRejected(ValueError):
    """A report the meter will not count."""


@dataclass(frozen=True)
class PoolStatus:
    """One organisation's month, as the gateway and billing see it.

    The administrator never gets the open month's `used`, `remaining` or
    `overage`: see `closed_months()`.
    """

    period: str  # "2026-10"
    seats: int  # the Work client's provisioned users
    used: int  # hosted steps served this month
    pool: int  # steps the Work client includes this month
    remaining: int  # steps left in the pool, never below 0
    overage: int  # steps above the pool to bill; 0 for own-endpoint organisations
    hard_cap: bool
    own_endpoint: bool
    work_client: bool


@dataclass(frozen=True)
class StepCheck:
    """May the gateway serve the next hosted step, and is it overage?"""

    allowed: bool
    refusal: Refusal | None
    meter_step: bool  # above the pool: billed as overage
    remaining: int  # what the gateway returns with the answer


@dataclass(frozen=True)
class Recorded:
    counted: bool  # False: this report id was already counted
    status: PoolStatus


def period_of(at: datetime) -> str:
    """The UTC calendar month `at` falls in, as "YYYY-MM"."""
    if at.tzinfo is None:
        raise ValueError("a timezone-aware datetime is required")
    return at.astimezone(UTC).strftime("%Y-%m")


def month_status(tenant: Tenant, period: str, used: int) -> PoolStatus:
    work_client = has_module(tenant, Module.WORK_CLIENT)
    own = tenant.own_endpoint_declared_at is not None
    seats = work_client_seats(tenant) if work_client else 0
    pool = enterprise_pool(seats)
    return PoolStatus(
        period=period,
        seats=seats,
        used=used,
        pool=pool,
        remaining=max(pool - used, 0),
        overage=0 if own else max(used - pool, 0),
        hard_cap=bool(tenant.guided_step_hard_cap),
        own_endpoint=own,
        work_client=work_client,
    )


def admit(status: PoolStatus) -> StepCheck:
    """The verdict for the next hosted step, from the month's status. Pure."""
    if not status.work_client:
        return StepCheck(False, Refusal.NO_WORK_CLIENT, False, 0)
    if status.own_endpoint:
        return StepCheck(False, Refusal.OWN_ENDPOINT, False, status.remaining)
    if status.hard_cap and status.used >= status.pool:
        return StepCheck(False, Refusal.POOL_USED, False, 0)
    verdict = decide(
        GuideTier.ENTERPRISE,
        used=status.used,
        seats=status.seats,
        own_endpoint=status.own_endpoint,
    )
    return StepCheck(True, None, verdict.meter_step, status.remaining)


async def _used(db: AsyncSession, tenant_id: uuid.UUID, period: str) -> int:
    steps = await db.scalar(
        select(GuidedStepCount.steps).where(
            GuidedStepCount.tenant_id == tenant_id, GuidedStepCount.period == period
        )
    )
    return steps or 0


# How many closed months the administrator's view lists, newest first.
CLOSED_MONTHS_SHOWN = 12


@dataclass(frozen=True)
class ClosedMonth:
    """A month that has ended, and the hosted steps it counted."""

    period: str  # "2026-09"
    used: int


async def closed_months(
    db: AsyncSession, tenant_id: uuid.UUID, *, now: datetime, limit: int = CLOSED_MONTHS_SHOWN
) -> list[ClosedMonth]:
    """The organisation's months before the one `now` falls in, newest first.

    What the administrator sees (promptly-guide decision P9): a month's count
    is shown only once the month has closed. The open month's count changes
    with every step, so in a small organisation, or read before and after one
    session, it would show one person's use (E4). Billing and the hard cap
    still read the open month through `pool_status()`.
    """
    rows = await db.execute(
        select(GuidedStepCount.period, GuidedStepCount.steps)
        .where(GuidedStepCount.tenant_id == tenant_id, GuidedStepCount.period < period_of(now))
        .order_by(GuidedStepCount.period.desc())
        .limit(limit)
    )
    return [ClosedMonth(period=period, used=steps) for period, steps in rows.all()]


async def pool_status(db: AsyncSession, tenant: Tenant, *, now: datetime) -> PoolStatus:
    period = period_of(now)
    return month_status(tenant, period, await _used(db, tenant.id, period))


async def check(db: AsyncSession, tenant: Tenant, *, now: datetime) -> StepCheck:
    """Called by the gateway before it serves a hosted step."""
    return admit(await pool_status(db, tenant, now=now))


def _insert(db: AsyncSession):  # noqa: ANN202 — dialect-specific Insert factory
    return postgresql.insert if db.get_bind().dialect.name == "postgresql" else sqlite.insert


async def record(
    db: AsyncSession,
    tenant: Tenant,
    *,
    report_id: str,
    served_at: datetime,
    steps: int = 1,
) -> Recorded:
    """Count `steps` hosted steps the gateway served at `served_at`.

    Idempotent on `report_id` within the month the steps were served, so a
    retried report counts once. The steps were already served, so this never
    refuses for the cap; `check()` is the gate. The caller commits.
    """
    if not _REPORT_ID_RE.match(report_id or ""):
        raise ReportRejected("report_id must be 1-64 of [A-Za-z0-9._:-]")
    if isinstance(steps, bool) or not isinstance(steps, int):
        raise ReportRejected("steps must be an integer")
    if not 1 <= steps <= MAX_STEPS_PER_REPORT:
        raise ReportRejected(f"steps must be 1 to {MAX_STEPS_PER_REPORT}")

    period = period_of(served_at)
    insert = _insert(db)
    first = await db.execute(
        insert(GuidedStepReceipt)
        .values(id=uuid.uuid4(), tenant_id=tenant.id, period=period, report_id=report_id)
        .on_conflict_do_nothing(index_elements=["tenant_id", "period", "report_id"])
    )
    counted = first.rowcount != 0
    if counted:
        await db.execute(
            insert(GuidedStepCount)
            .values(id=uuid.uuid4(), tenant_id=tenant.id, period=period, steps=steps)
            .on_conflict_do_update(
                index_elements=["tenant_id", "period"],
                set_={"steps": GuidedStepCount.steps + steps},
            )
        )
    return Recorded(counted, month_status(tenant, period, await _used(db, tenant.id, period)))
