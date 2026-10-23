"""Posting the Work client's guided-step overage to Stripe (promptly-guide #62).

Stripe is replaced by an in-memory fake patched over the ``stripe_client``
wrappers. Every other wrapper raises, so any other Stripe call fails the test.
Pool sizes are worked out with ``enterprise_pool()``, never written here, and
no amount appears: the meter counts steps, and Stripe prices them.
"""

from __future__ import annotations

import inspect
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingOutbox
from app.models.tenant import Tenant
from app.services.billing import step_overage, stripe_client
from app.services.billing.catalog import GUIDED_STEP_EVENT
from app.services.entitlements import WORK_CLIENT_LOOKUP_KEY
from app.services.guided_step_meter import MAX_STEPS_PER_REPORT, record
from app.services.guided_steps import enterprise_pool
from tests.conftest import TestSessionLocal, ensure_tenant

pytestmark = [pytest.mark.unit]

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
SEATS = 3
POOL = enterprise_pool(SEATS)


class FakeStripe:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.fail_with: list[Exception] = []

    def create_meter_event(self, **kw):
        self.events.append(kw)
        if self.fail_with:
            raise self.fail_with.pop(0)
        return {"object": "billing.meter_event", "identifier": kw["identifier"]}


@pytest.fixture
def fake(monkeypatch) -> FakeStripe:
    f = FakeStripe()

    def forbidden(name):
        def call(*a, **kw):
            raise AssertionError(f"stripe_client.{name} called while posting overage")

        return call

    for name, fn in inspect.getmembers(stripe_client, inspect.isfunction):
        if fn.__module__ == stripe_client.__name__ and not name.startswith("_"):
            monkeypatch.setattr(stripe_client, name, forbidden(name))
    monkeypatch.setattr(stripe_client, "create_meter_event", f.create_meter_event)
    return f


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with TestSessionLocal() as session:
        yield session


async def _org(db: AsyncSession, *, seats: int = SEATS, customer: str | None = "", **kw) -> Tenant:
    tenant = await ensure_tenant(db, uuid.uuid4())
    tenant.stripe_subscriptions = {
        "sub_wc": {
            "kind": "enterprise",
            "status": "active",
            "items": {WORK_CLIENT_LOOKUP_KEY: seats},
        }
    }
    tenant.stripe_customer_id = f"cus_{tenant.id.hex[:12]}" if customer == "" else customer
    for k, v in kw.items():
        setattr(tenant, k, v)
    await db.commit()
    return tenant


async def _serve(db: AsyncSession, tenant: Tenant, steps: int, *, at: datetime = NOW) -> None:
    while steps > 0:
        n = min(steps, MAX_STEPS_PER_REPORT)
        await record(db, tenant, report_id=uuid.uuid4().hex, served_at=at, steps=n)
        steps -= n
    await db.commit()


async def _rows(db: AsyncSession, tenant: Tenant) -> list[BillingOutbox]:
    rows = await db.execute(
        select(BillingOutbox)
        .where(BillingOutbox.tenant_id == tenant.id)
        .order_by(BillingOutbox.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


def _ours(fake: FakeStripe, tenant: Tenant) -> list[dict]:
    return [
        e for e in fake.events if e["payload"]["stripe_customer_id"] == tenant.stripe_customer_id
    ]


# ── What is posted ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nothing_is_posted_within_the_pool(db, fake) -> None:
    tenant = await _org(db)
    await _serve(db, tenant, POOL)
    await step_overage.run(db, now=NOW)
    assert _ours(fake, tenant) == []
    assert await _rows(db, tenant) == []


@pytest.mark.asyncio
async def test_overage_is_posted_once_with_a_stable_identifier(db, fake) -> None:
    tenant = await _org(db)
    await _serve(db, tenant, POOL + 7)
    report = await step_overage.run(db, now=NOW)

    ident = f"{GUIDED_STEP_EVENT}:{tenant.id}:2026-10:7"
    assert step_overage.identifier(tenant.id, "2026-10", 7) == ident
    assert _ours(fake, tenant) == [
        {
            "event_name": GUIDED_STEP_EVENT,
            "payload": {"stripe_customer_id": tenant.stripe_customer_id, "value": "7"},
            "identifier": ident,
            "timestamp": int(NOW.timestamp()),
            "idempotency_key": ident,
        }
    ]
    (row,) = await _rows(db, tenant)
    assert row.operation == step_overage.OPERATION
    assert row.succeeded_at is not None and row.attempt_count == 1
    assert step_overage.Increment(tenant.id, "2026-10", 7, 7) in report.queued

    # The next hour, with no new steps, posts nothing.
    await step_overage.run(db, now=NOW + timedelta(hours=1))
    assert len(_ours(fake, tenant)) == 1


@pytest.mark.asyncio
async def test_each_hour_posts_only_the_rise(db, fake) -> None:
    tenant = await _org(db)
    await _serve(db, tenant, POOL + 5)
    await step_overage.run(db, now=NOW)
    await _serve(db, tenant, 3)
    await step_overage.run(db, now=NOW + timedelta(hours=1))

    events = _ours(fake, tenant)
    assert [e["payload"]["value"] for e in events] == ["5", "3"]
    assert [e["identifier"].rsplit(":", 1)[1] for e in events] == ["5", "8"]
    assert sum(int(e["payload"]["value"]) for e in events) == 8


@pytest.mark.asyncio
async def test_queueing_twice_queues_one_row(db, fake) -> None:
    tenant = await _org(db)
    await _serve(db, tenant, POOL + 2)
    assert await step_overage.enqueue_overage(db, tenant, at=NOW) is not None
    assert await step_overage.enqueue_overage(db, tenant, at=NOW) is None
    await db.commit()
    assert len(await _rows(db, tenant)) == 1


@pytest.mark.asyncio
async def test_a_fall_in_overage_posts_nothing_until_it_rises_past_the_last_total(db, fake) -> None:
    tenant = await _org(db)
    await _serve(db, tenant, POOL + 10)
    await step_overage.run(db, now=NOW)

    # More seats this month: the pool grows and the overage falls.
    tenant.stripe_subscriptions = {
        "sub_wc": {"kind": "enterprise", "status": "active", "items": {WORK_CLIENT_LOOKUP_KEY: 4}}
    }
    await db.commit()
    bigger = enterprise_pool(4)
    await _serve(db, tenant, bigger - POOL - 10 + 4)  # overage is now 4: below 10
    await step_overage.run(db, now=NOW + timedelta(hours=1))
    assert len(_ours(fake, tenant)) == 1

    await _serve(db, tenant, 9)  # overage is now 13
    await step_overage.run(db, now=NOW + timedelta(hours=2))
    assert [e["payload"]["value"] for e in _ours(fake, tenant)] == ["10", "3"]


@pytest.mark.asyncio
async def test_own_endpoint_and_customerless_organisations_post_nothing(db, fake) -> None:
    own = await _org(db, own_endpoint_declared_at=NOW - timedelta(days=1))
    nobody = await _org(db, customer=None)
    for t in (own, nobody):
        await _serve(db, t, POOL + 4)
    await step_overage.run(db, now=NOW)
    assert fake.events == []
    assert await _rows(db, own) == [] and await _rows(db, nobody) == []


@pytest.mark.asyncio
async def test_the_month_turn_settles_last_month_in_last_month(db, fake) -> None:
    tenant = await _org(db)
    late = datetime(2026, 10, 31, 23, 50, tzinfo=UTC)
    await step_overage.run(db, now=late - timedelta(minutes=30))  # the month's last run
    await _serve(db, tenant, POOL + 6, at=late)  # served after it
    assert _ours(fake, tenant) == []

    first = datetime(2026, 11, 1, 0, 5, tzinfo=UTC)
    await _serve(db, tenant, POOL + 1, at=first)  # November has its own overage
    await step_overage.run(db, now=first)
    events = _ours(fake, tenant)
    assert [(e["identifier"].split(":")[2], e["payload"]["value"]) for e in events] == [
        ("2026-10", "6"),
        ("2026-11", "1"),
    ]
    assert events[0]["timestamp"] == int(datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC).timestamp())
    assert events[1]["timestamp"] == int(first.timestamp())


# ── Retries ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_retry_resends_the_same_event(db, fake) -> None:
    """A lost response is retried with the same identifier and idempotency key,
    so Stripe counts the increment once."""
    tenant = await _org(db)
    await _serve(db, tenant, POOL + 4)
    fake.fail_with = [stripe.APIConnectionError("timed out")]
    first = await step_overage.run(db, now=NOW)
    assert first.processed.retrying == 1

    (row,) = await _rows(db, tenant)
    assert row.succeeded_at is None and row.attempt_count == 1
    assert row.next_attempt_at > NOW and "APIConnectionError" in row.last_error

    await step_overage.run(db, now=NOW + timedelta(seconds=30))  # not due yet
    assert len(_ours(fake, tenant)) == 1

    await step_overage.run(db, now=NOW + timedelta(hours=1))
    a, b = _ours(fake, tenant)
    assert a == b
    (row,) = await _rows(db, tenant)
    assert row.succeeded_at is not None and row.attempt_count == 2


@pytest.mark.asyncio
async def test_a_rejected_event_is_dead_lettered_and_never_requeued(db, fake) -> None:
    tenant = await _org(db)
    await _serve(db, tenant, POOL + 4)
    fake.fail_with = [stripe.InvalidRequestError("No such customer", "payload")]
    report = await step_overage.run(db, now=NOW)
    assert report.processed.dead_lettered == 1

    (row,) = await _rows(db, tenant)
    assert row.dead_lettered_at is not None and row.succeeded_at is None
    assert "InvalidRequestError" in row.last_error

    # Its steps are left for a person; only a later rise is posted.
    await _serve(db, tenant, 2)
    await step_overage.run(db, now=NOW + timedelta(hours=1))
    events = _ours(fake, tenant)
    assert [e["payload"]["value"] for e in events] == ["4", "2"]
    assert len(await _rows(db, tenant)) == 2


@pytest.mark.asyncio
async def test_retries_stop_inside_stripes_idempotency_window(db, fake) -> None:
    tenant = await _org(db)
    await _serve(db, tenant, POOL + 1)
    fake.fail_with = [stripe.APIError("down")] * 100
    at = NOW
    while at - NOW < step_overage.RETRY_WINDOW + timedelta(hours=2):
        await step_overage.run(db, now=at)
        at += timedelta(hours=1)

    (row,) = await _rows(db, tenant)
    assert row.dead_lettered_at is not None
    assert row.dead_lettered_at - NOW < timedelta(hours=24)
    assert len({e["identifier"] for e in _ours(fake, tenant)}) == 1


@pytest.mark.asyncio
async def test_a_dry_run_queues_nothing_and_calls_no_stripe(db, fake) -> None:
    tenant = await _org(db)
    await _serve(db, tenant, POOL + 3)
    tid = tenant.id  # the dry run's rollback expires `tenant`
    report = await step_overage.run(db, now=NOW, send=False)
    assert [i.value for i in report.queued if i.tenant_id == tid] == [3]
    assert fake.events == []
    rows = await db.scalars(select(BillingOutbox).where(BillingOutbox.tenant_id == tid))
    assert rows.all() == []


@pytest.mark.asyncio
async def test_other_outbox_operations_are_left_alone(db, fake) -> None:
    from app.services.billing.outbox import enqueue

    tenant = await _org(db)
    await enqueue(db, operation="sync_seat", tenant_id=tenant.id, payload={"quantity": 5})
    await db.commit()
    await step_overage.run(db, now=NOW + timedelta(hours=1))
    (row,) = await _rows(db, tenant)
    assert row.operation == "sync_seat" and row.attempt_count == 0
    assert fake.events == []


# ── On the real SDK's objects (no fake over the wrappers) ─────────────────


@pytest.mark.asyncio
async def test_posting_through_the_real_wrapper_and_a_real_meter_event(db, monkeypatch) -> None:
    """stripe-python 16 returns a ``MeterEvent`` (not a dict); the wrapper hands
    back a plain dict and passes the idempotency key as a request option."""
    calls: list[dict] = []

    def create(**params):
        calls.append(params)
        body = {k: v for k, v in params.items() if k != "idempotency_key"}
        return stripe.billing.MeterEvent.construct_from(
            {"object": "billing.meter_event", "created": params["timestamp"], **body},
            "sk_test_offline",
        )

    monkeypatch.setattr(stripe.billing.MeterEvent, "create", create)

    event = stripe_client.create_meter_event(
        event_name=GUIDED_STEP_EVENT,
        payload={"stripe_customer_id": "cus_x", "value": "2"},
        identifier="guided_step:x:2026-10:2",
        timestamp=int(NOW.timestamp()),
        idempotency_key="guided_step:x:2026-10:2",
    )
    assert type(event) is dict and type(event["payload"]) is dict
    assert event["payload"]["value"] == "2"
    assert calls[-1]["idempotency_key"] == "guided_step:x:2026-10:2"

    tenant = await _org(db)
    await _serve(db, tenant, POOL + 2)
    report = await step_overage.run(db, now=NOW)
    assert report.processed.sent >= 1
    (row,) = await _rows(db, tenant)
    assert row.succeeded_at is not None
    assert calls[-1]["identifier"] == calls[-1]["idempotency_key"] == row.payload["identifier"]


def test_the_command_refuses_a_live_key_without_live(monkeypatch, capsys) -> None:
    from app.config import get_settings
    from scripts import stripe_step_meter

    monkeypatch.setattr(get_settings(), "stripe_secret_key", "")
    assert stripe_step_meter.main([]) == 2
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "rk_live_x")
    assert stripe_step_meter.main([]) == 2
    assert "--live" in capsys.readouterr().err
