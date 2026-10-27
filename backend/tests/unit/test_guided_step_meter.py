"""The Work client's guided-step meter and pool cap (promptly-guide #62).

Pool sizes are worked out with `enterprise_pool()` from a made-up steps-per-user
figure (`STEPS_PER_USER`), configured by a fixture: the plan's figure is a
commercial term and is never written here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.guided_step import GuidedStepCount, GuidedStepReceipt
from app.models.tenant import Tenant
from app.services.entitlements import WORK_CLIENT_LOOKUP_KEY, work_client_seats
from app.services.guided_step_meter import (
    MAX_STEPS_PER_REPORT,
    Refusal,
    ReportRejected,
    admit,
    check,
    month_status,
    period_of,
    pool_status,
    record,
)
from app.services.guided_steps import enterprise_pool
from tests.conftest import TestSessionLocal, ensure_tenant

pytestmark = [pytest.mark.unit]

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
SEATS = 3


# Made up: the real allowance is a commercial term, set per deployment.
STEPS_PER_USER = 7


@pytest.fixture(autouse=True)
def _pool_configured(monkeypatch):
    monkeypatch.setattr(get_settings(), "guided_steps_pool_per_user", STEPS_PER_USER)


def _subs(seats: int = SEATS, status: str = "active", **extra: dict) -> dict:
    subs = {
        "sub_wc": {"kind": "enterprise", "status": status, "items": {WORK_CLIENT_LOOKUP_KEY: seats}}
    }
    subs.update(extra)
    return subs


def _tenant(**kw) -> Tenant:
    t = Tenant(name="t", slug="t")
    t.stripe_subscriptions = _subs()
    t.own_endpoint_declared_at = None
    t.guided_step_hard_cap = False
    for k, v in kw.items():
        setattr(t, k, v)
    return t


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with TestSessionLocal() as session:
        yield session


async def _work_tenant(db: AsyncSession, **kw) -> Tenant:
    tenant = await ensure_tenant(db, uuid.uuid4())
    tenant.stripe_subscriptions = _subs()
    for k, v in kw.items():
        setattr(tenant, k, v)
    await db.flush()
    return tenant


# ── The pool's size ────────────────────────────────────────────────────────


def test_seats_are_the_work_client_quantity() -> None:
    assert work_client_seats(_tenant()) == SEATS


def test_no_work_client_no_seats() -> None:
    assert work_client_seats(_tenant(stripe_subscriptions={})) == 0
    canceled = _tenant(stripe_subscriptions=_subs(status="canceled"))
    assert work_client_seats(canceled) == 0


def test_a_failed_payment_keeps_the_free_work_client_pool() -> None:
    assert work_client_seats(_tenant(stripe_subscriptions=_subs(status="past_due"))) == SEATS


def test_a_second_subscription_never_shrinks_the_pool() -> None:
    other = {"status": "active", "items": {WORK_CLIENT_LOOKUP_KEY: 1}}
    assert work_client_seats(_tenant(stripe_subscriptions=_subs(sub_b=other))) == SEATS


# ── Months ─────────────────────────────────────────────────────────────────


def test_period_is_the_utc_month() -> None:
    assert period_of(NOW) == "2026-10"
    # 00:30 on 1 November in Oslo is still October in UTC.
    oslo = timezone(timedelta(hours=1))
    assert period_of(datetime(2026, 11, 1, 0, 30, tzinfo=oslo)) == "2026-10"


def test_period_refuses_a_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone"):
        period_of(datetime(2026, 10, 1))


# ── The verdict ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_under_the_pool_is_served_and_not_metered(db: AsyncSession) -> None:
    tenant = await _work_tenant(db)
    verdict = await check(db, tenant, now=NOW)
    assert verdict.allowed and verdict.refusal is None
    assert not verdict.meter_step
    assert verdict.remaining == enterprise_pool(SEATS)


@pytest.mark.asyncio
async def test_above_the_pool_is_served_and_metered_when_no_hard_cap(db: AsyncSession) -> None:
    tenant = await _work_tenant(db)
    pool = enterprise_pool(SEATS)
    await record(db, tenant, report_id="r1", served_at=NOW, steps=pool)
    verdict = await check(db, tenant, now=NOW)
    assert verdict.allowed and verdict.meter_step and verdict.remaining == 0

    await record(db, tenant, report_id="r2", served_at=NOW, steps=2)
    assert (await pool_status(db, tenant, now=NOW)).overage == 2


@pytest.mark.asyncio
async def test_hard_cap_is_off_for_a_new_tenant(db: AsyncSession) -> None:
    tenant = await ensure_tenant(db, uuid.uuid4())
    await db.commit()
    await db.refresh(tenant)
    assert tenant.guided_step_hard_cap is False


@pytest.mark.asyncio
async def test_hard_cap_stops_at_the_pool_until_the_month_turns(db: AsyncSession) -> None:
    tenant = await _work_tenant(db, guided_step_hard_cap=True)
    pool = enterprise_pool(SEATS)
    await record(db, tenant, report_id="r1", served_at=NOW, steps=pool - 1)
    assert (await check(db, tenant, now=NOW)).allowed

    await record(db, tenant, report_id="r2", served_at=NOW)
    verdict = await check(db, tenant, now=NOW)
    assert not verdict.allowed and verdict.refusal is Refusal.POOL_USED

    next_month = datetime(2026, 11, 1, tzinfo=UTC)
    assert (await check(db, tenant, now=next_month)).allowed


def test_no_work_client_is_not_served() -> None:
    status = month_status(_tenant(stripe_subscriptions={}), "2026-10", 0)
    verdict = admit(status)
    assert not verdict.allowed and verdict.refusal is Refusal.NO_WORK_CLIENT


def test_own_endpoint_is_not_served_hosted_steps_nor_metered() -> None:
    tenant = _tenant(own_endpoint_declared_at=NOW)
    status = month_status(tenant, "2026-10", enterprise_pool(SEATS) + 5)
    assert status.overage == 0
    verdict = admit(status)
    assert not verdict.allowed and verdict.refusal is Refusal.OWN_ENDPOINT
    assert not verdict.meter_step


# ── The pool not configured: fail closed ───────────────────────────────────


def _unconfigured(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "guided_steps_pool_per_user", None)


def test_an_unconfigured_pool_is_unknown_not_zero(monkeypatch) -> None:
    _unconfigured(monkeypatch)
    status = month_status(_tenant(), "2026-10", 5)
    assert status.pool is None and status.remaining is None and status.overage is None
    assert status.used == 5 and status.seats == SEATS


def test_an_unconfigured_pool_serves_no_hosted_step(monkeypatch) -> None:
    _unconfigured(monkeypatch)
    verdict = admit(month_status(_tenant(), "2026-10", 0))
    assert not verdict.allowed and verdict.refusal is Refusal.POOL_UNKNOWN
    assert not verdict.meter_step and verdict.remaining == 0


def test_an_unconfigured_pool_still_has_no_overage_for_an_own_endpoint(monkeypatch) -> None:
    _unconfigured(monkeypatch)
    status = month_status(_tenant(own_endpoint_declared_at=NOW), "2026-10", 5)
    assert status.overage == 0
    assert admit(status).refusal is Refusal.OWN_ENDPOINT


@pytest.mark.asyncio
async def test_an_unconfigured_pool_still_counts_steps_already_served(
    db: AsyncSession, monkeypatch
) -> None:
    _unconfigured(monkeypatch)
    tenant = await _work_tenant(db)
    recorded = await record(db, tenant, report_id="r1", served_at=NOW, steps=3)
    assert recorded.counted and recorded.status.used == 3
    assert (await check(db, tenant, now=NOW)).refusal is Refusal.POOL_UNKNOWN


# ── Recording ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_retried_report_counts_once(db: AsyncSession) -> None:
    tenant = await _work_tenant(db)
    first = await record(db, tenant, report_id="step-abc", served_at=NOW, steps=3)
    again = await record(db, tenant, report_id="step-abc", served_at=NOW, steps=3)
    await db.commit()
    assert first.counted and not again.counted
    assert first.status.used == again.status.used == 3


@pytest.mark.asyncio
async def test_steps_count_in_the_month_they_were_served(db: AsyncSession) -> None:
    tenant = await _work_tenant(db)
    await record(db, tenant, report_id="a", served_at=datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    await record(db, tenant, report_id="b", served_at=NOW)
    await record(db, tenant, report_id="c", served_at=NOW)
    assert (await pool_status(db, tenant, now=NOW)).used == 2
    september = datetime(2026, 9, 15, tzinfo=UTC)
    assert (await pool_status(db, tenant, now=september)).used == 1


@pytest.mark.asyncio
async def test_organisations_are_counted_apart(db: AsyncSession) -> None:
    one = await _work_tenant(db)
    two = await _work_tenant(db)
    await record(db, one, report_id="same-id", served_at=NOW, steps=4)
    second = await record(db, two, report_id="same-id", served_at=NOW, steps=1)
    assert second.counted
    assert (await pool_status(db, one, now=NOW)).used == 4
    assert (await pool_status(db, two, now=NOW)).used == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("report_id", ["", "x" * 65, "has space", "ünïcode", None])
async def test_a_malformed_report_id_is_rejected(db: AsyncSession, report_id) -> None:  # noqa: ANN001
    tenant = await _work_tenant(db)
    with pytest.raises(ReportRejected):
        await record(db, tenant, report_id=report_id, served_at=NOW)


@pytest.mark.asyncio
@pytest.mark.parametrize("steps", [0, -1, MAX_STEPS_PER_REPORT + 1, True, 1.5])
async def test_a_malformed_step_count_is_rejected(db: AsyncSession, steps) -> None:  # noqa: ANN001
    tenant = await _work_tenant(db)
    with pytest.raises(ReportRejected):
        await record(db, tenant, report_id="r", served_at=NOW, steps=steps)
    assert (await pool_status(db, tenant, now=NOW)).used == 0


# ── Nothing per person (E4) ────────────────────────────────────────────────


def test_the_meter_stores_no_person_and_no_time() -> None:
    assert {c.name for c in GuidedStepCount.__table__.columns} == {
        "id",
        "tenant_id",
        "period",
        "steps",
    }
    assert {c.name for c in GuidedStepReceipt.__table__.columns} == {
        "id",
        "tenant_id",
        "period",
        "report_id",
    }


@pytest.mark.asyncio
async def test_one_counter_row_per_organisation_and_month(db: AsyncSession) -> None:
    tenant = await _work_tenant(db)
    for i in range(5):
        await record(db, tenant, report_id=f"r{i}", served_at=NOW)
    rows = (
        (await db.execute(select(GuidedStepCount).where(GuidedStepCount.tenant_id == tenant.id)))
        .scalars()
        .all()
    )
    assert [(r.period, r.steps) for r in rows] == [("2026-10", 5)]
