"""Sending the queued seat syncs (``sync_seat`` rows) to Stripe.

Stripe is replaced by an in-memory fake patched over the ``stripe_client``
wrappers. Every other wrapper raises, so any other Stripe call fails the test.
Seat counts are worked out with ``seat_sync.seat_quantity``; no price or
amount appears anywhere: Stripe prices the seats.
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
from app.models.tenant import SubscriptionStatus, Tenant
from app.models.user import User
from app.services.billing import outbox, seat_outbox, seat_sync, step_overage, stripe_client
from tests.conftest import TestSessionLocal, ensure_tenant

pytestmark = [pytest.mark.unit]

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
MANY = seat_sync.SEAT_FLOOR + 3  # activated users above the floor


class FakeStripe:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_with: list[Exception] = []

    def modify_subscription_item(self, item_id: str, **kw):
        self.calls.append({"item": item_id, **kw})
        if self.fail_with:
            raise self.fail_with.pop(0)
        return {"object": "subscription_item", "id": item_id, "quantity": kw["quantity"]}


@pytest.fixture
def fake(monkeypatch) -> FakeStripe:
    f = FakeStripe()

    def forbidden(name):
        def call(*a, **kw):
            raise AssertionError(f"stripe_client.{name} called while syncing seats")

        return call

    for name, fn in inspect.getmembers(stripe_client, inspect.isfunction):
        if fn.__module__ == stripe_client.__name__ and not name.startswith("_"):
            monkeypatch.setattr(stripe_client, name, forbidden(name))
    monkeypatch.setattr(stripe_client, "modify_subscription_item", f.modify_subscription_item)
    return f


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with TestSessionLocal() as session:
        yield session


async def _org(
    db: AsyncSession,
    *,
    activated: int = MANY,
    status: SubscriptionStatus | None = SubscriptionStatus.ACTIVE,
) -> Tenant:
    tenant = await ensure_tenant(db, uuid.uuid4())
    tenant.stripe_customer_id = f"cus_{tenant.id.hex[:12]}"
    tenant.stripe_subscription_item_id = f"si_{tenant.id.hex[:12]}"
    tenant.subscription_status = status
    await db.flush()
    await _activate(db, tenant, activated)
    return tenant


async def _activate(db: AsyncSession, tenant: Tenant, n: int) -> None:
    for _ in range(n):
        uid = uuid.uuid4()
        db.add(
            User(
                id=uid,
                email=f"u-{uid.hex[:12]}@acme.test",
                hashed_password="x",
                full_name="User",
                tenant_id=tenant.id,
                first_login_at=NOW - timedelta(days=1),
            )
        )
    await db.commit()


async def _queue(db: AsyncSession, tenant: Tenant, quantity: int, *, at: datetime = NOW) -> None:
    """What ``seat_sync.sync_seat_count`` queues when Stripe is unreachable."""
    await outbox.enqueue(
        db, operation=seat_sync.OPERATION, tenant_id=tenant.id, payload={"quantity": quantity}
    )
    rows = await _rows(db, tenant)
    rows[-1].next_attempt_at = at
    await db.commit()


async def _rows(db: AsyncSession, tenant: Tenant) -> list[BillingOutbox]:
    rows = await db.execute(
        select(BillingOutbox)
        .where(BillingOutbox.tenant_id == tenant.id)
        .order_by(BillingOutbox.id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def _tenant(db: AsyncSession, tid: uuid.UUID) -> Tenant:
    return (
        await db.execute(
            select(Tenant).where(Tenant.id == tid).execution_options(populate_existing=True)
        )
    ).scalar_one()


def _ours(fake: FakeStripe, tenant: Tenant) -> list[dict]:
    return [c for c in fake.calls if c["item"] == tenant.stripe_subscription_item_id]


# ── What is sent ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_queued_sync_is_sent_once_with_its_key(db, fake) -> None:
    tenant = await _org(db)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    report = await seat_outbox.run(db, now=NOW)

    (row,) = await _rows(db, tenant)
    want = seat_sync.seat_quantity(MANY)
    key = f"sync_seat:{tenant.id}:{row.id}:{tenant.stripe_subscription_item_id}:{want}"
    assert (
        seat_outbox.idempotency_key(tenant.id, row.id, tenant.stripe_subscription_item_id, want)
        == key
    )
    assert _ours(fake, tenant) == [
        {
            "item": tenant.stripe_subscription_item_id,
            "quantity": want,
            "proration_behavior": "create_prorations",
            "idempotency_key": key,
        }
    ]
    assert row.succeeded_at is not None and row.attempt_count == 1 and row.last_error is None
    assert row.payload["sent_quantity"] == want and row.payload["idempotency_key"] == key
    assert (await _tenant(db, tenant.id)).seats_billed == want
    assert seat_outbox.Planned(tenant.id, row.id, 0, quantity=want) in report.planned

    # The next run has nothing left to send.
    await seat_outbox.run(db, now=NOW + timedelta(hours=1))
    assert len(_ours(fake, tenant)) == 1


@pytest.mark.asyncio
async def test_the_floor_applies_to_what_is_sent(db, fake) -> None:
    tenant = await _org(db, activated=1)
    await _queue(db, tenant, seat_sync.seat_quantity(1))
    await seat_outbox.run(db, now=NOW)
    (call,) = _ours(fake, tenant)
    assert call["quantity"] == seat_sync.SEAT_FLOOR


@pytest.mark.asyncio
async def test_the_count_is_read_when_sent_not_when_queued(db, fake) -> None:
    """Users who logged in while the row waited are counted: an old row never
    sets the subscription below the activated users."""
    tenant = await _org(db)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    await _activate(db, tenant, 2)
    await seat_outbox.run(db, now=NOW)
    (call,) = _ours(fake, tenant)
    assert call["quantity"] == seat_sync.seat_quantity(MANY + 2)
    (row,) = await _rows(db, tenant)
    assert row.payload["quantity"] == seat_sync.seat_quantity(MANY)  # as queued


@pytest.mark.asyncio
async def test_only_the_newest_row_is_sent_and_older_ones_are_superseded(db, fake) -> None:
    tenant = await _org(db)
    for extra in range(3):
        await _queue(db, tenant, seat_sync.seat_quantity(MANY - 2 + extra))
    report = await seat_outbox.run(db, now=NOW)

    assert len(_ours(fake, tenant)) == 1
    *older, newest = await _rows(db, tenant)
    assert newest.succeeded_at is not None and newest.attempt_count == 1
    for row in older:
        assert row.succeeded_at is not None and row.attempt_count == 0
        assert row.payload["superseded_by"] == newest.id
    (planned,) = [p for p in report.planned if p.tenant_id == tenant.id]
    assert planned.outbox_id == newest.id and planned.superseded == 2


@pytest.mark.asyncio
async def test_other_outbox_operations_are_left_alone(db, fake) -> None:
    tenant = await _org(db)
    await outbox.enqueue(
        db, operation=step_overage.OPERATION, tenant_id=tenant.id, payload={"value": 1}
    )
    await db.commit()
    await seat_outbox.run(db, now=NOW + timedelta(hours=1))
    (row,) = await _rows(db, tenant)
    assert row.operation == step_overage.OPERATION and row.attempt_count == 0
    assert row.succeeded_at is None
    assert _ours(fake, tenant) == []


@pytest.mark.asyncio
async def test_a_cancelled_subscription_is_skipped_without_a_call(db, fake) -> None:
    tenant = await _org(db, status=SubscriptionStatus.CANCELED)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    report = await seat_outbox.run(db, now=NOW)
    assert _ours(fake, tenant) == []
    (row,) = await _rows(db, tenant)
    assert row.succeeded_at is not None and row.attempt_count == 0
    assert row.payload["skipped"] == "no_active_subscription"
    assert any(p.tenant_id == tenant.id and p.skipped for p in report.planned)


@pytest.mark.asyncio
async def test_a_row_is_not_sent_before_it_is_due(db, fake) -> None:
    tenant = await _org(db)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY), at=NOW + timedelta(minutes=10))
    await seat_outbox.run(db, now=NOW)
    assert _ours(fake, tenant) == []
    await seat_outbox.run(db, now=NOW + timedelta(minutes=10))
    assert len(_ours(fake, tenant)) == 1


# ── Retries ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_retry_resends_the_same_request(db, fake) -> None:
    """A lost response is retried with the same idempotency key, so Stripe
    applies the change once."""
    tenant = await _org(db)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    fake.fail_with = [stripe.APIConnectionError("timed out")]
    first = await seat_outbox.run(db, now=NOW)
    assert first.processed.retrying >= 1

    (row,) = await _rows(db, tenant)
    assert row.succeeded_at is None and row.attempt_count == 1
    assert row.next_attempt_at.replace(tzinfo=UTC) > NOW and "APIConnectionError" in row.last_error

    await seat_outbox.run(db, now=NOW + timedelta(seconds=30))  # not due yet
    assert len(_ours(fake, tenant)) == 1

    await seat_outbox.run(db, now=NOW + timedelta(hours=1))
    a, b = _ours(fake, tenant)
    assert a == b
    (row,) = await _rows(db, tenant)
    assert row.succeeded_at is not None and row.attempt_count == 2
    assert (await _tenant(db, tenant.id)).seats_billed == a["quantity"]


@pytest.mark.asyncio
async def test_a_new_count_between_retries_gets_a_new_key(db, fake) -> None:
    """Stripe refuses a key reused with other parameters, so a changed count is
    a new request; setting a quantity twice is harmless."""
    tenant = await _org(db)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    fake.fail_with = [stripe.RateLimitError("slow down")]
    await seat_outbox.run(db, now=NOW)
    await _activate(db, tenant, 1)
    await seat_outbox.run(db, now=NOW + timedelta(hours=1))

    a, b = _ours(fake, tenant)
    assert (a["quantity"], b["quantity"]) == (
        seat_sync.seat_quantity(MANY),
        seat_sync.seat_quantity(MANY + 1),
    )
    assert a["idempotency_key"] != b["idempotency_key"]
    assert a["idempotency_key"].rsplit(":", 1)[0] == b["idempotency_key"].rsplit(":", 1)[0]


@pytest.mark.asyncio
async def test_a_rejected_sync_is_dead_lettered_at_once(db, fake) -> None:
    tenant = await _org(db)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    fake.fail_with = [stripe.InvalidRequestError("No such subscription item", "id")]
    report = await seat_outbox.run(db, now=NOW)
    assert report.processed.dead_lettered >= 1

    (row,) = await _rows(db, tenant)
    assert row.dead_lettered_at is not None and row.succeeded_at is None
    assert "InvalidRequestError" in row.last_error
    await seat_outbox.run(db, now=NOW + timedelta(hours=2))
    assert len(_ours(fake, tenant)) == 1


@pytest.mark.asyncio
async def test_retries_stop_inside_stripes_idempotency_window(db, fake) -> None:
    tenant = await _org(db)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    fake.fail_with = [stripe.APIError("down")] * 100
    at = NOW
    while at - NOW < outbox.RETRY_WINDOW + timedelta(hours=2):
        await seat_outbox.run(db, now=at)
        at += timedelta(hours=1)

    (row,) = await _rows(db, tenant)
    assert row.dead_lettered_at is not None
    assert row.dead_lettered_at.replace(tzinfo=UTC) - NOW < timedelta(hours=24)
    assert len({c["idempotency_key"] for c in _ours(fake, tenant)}) == 1
    # The overage processor keeps the same window.
    assert step_overage.RETRY_WINDOW == outbox.RETRY_WINDOW


@pytest.mark.asyncio
async def test_one_organisations_failure_does_not_hold_up_another(db, fake, monkeypatch) -> None:
    bad, good = await _org(db), await _org(db)
    for t in (bad, good):
        await _queue(db, t, seat_sync.seat_quantity(MANY))
    real = fake.modify_subscription_item
    # A failed organisation's rollback expires every loaded object: read ids first.
    bad_id, bad_item = bad.id, bad.stripe_subscription_item_id
    good_item = good.stripe_subscription_item_id

    def flaky(item_id, **kw):
        if item_id == bad_item:
            raise RuntimeError("not a Stripe error")
        return real(item_id, **kw)

    monkeypatch.setattr(stripe_client, "modify_subscription_item", flaky)
    report = await seat_outbox.run(db, now=NOW)
    assert bad_id in report.failed_tenants
    assert [c["item"] for c in fake.calls if c["item"] == good_item] == [good_item]
    (row,) = await _rows(db, await _tenant(db, bad_id))
    assert row.succeeded_at is None and row.attempt_count == 0  # rolled back


@pytest.mark.asyncio
async def test_a_dry_run_changes_nothing_and_calls_no_stripe(db, fake) -> None:
    tenant = await _org(db)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    tid = tenant.id  # the dry run's rollback expires `tenant`
    report = await seat_outbox.run(db, now=NOW, send=False)
    (planned,) = [p for p in report.planned if p.tenant_id == tid]
    assert planned.quantity == seat_sync.seat_quantity(MANY) and planned.superseded == 1
    assert fake.calls == []
    rows = (await db.scalars(select(BillingOutbox).where(BillingOutbox.tenant_id == tid))).all()
    assert len(rows) == 2
    assert all(r.succeeded_at is None and r.attempt_count == 0 for r in rows)


# ── On the real SDK's objects (no fake over the wrappers) ─────────────────


@pytest.mark.asyncio
async def test_syncing_through_the_real_wrapper_and_a_real_subscription_item(
    db, monkeypatch
) -> None:
    """stripe-python 16 returns a ``SubscriptionItem`` (not a dict). The real
    ``SubscriptionItem.modify`` runs; only the HTTP requestor is replaced. The
    wrapper hands back a plain dict, and the idempotency key travels as a
    request option, not as a form parameter."""
    from stripe._api_requestor import _APIRequestor

    sent: list[dict] = []

    def request(self, method, url, params=None, options=None, **kw):
        sent.append({"method": method, "url": url, "params": params, "options": options})
        return stripe.SubscriptionItem.construct_from(
            {
                "id": url.rsplit("/", 1)[1],
                "object": "subscription_item",
                "quantity": params["quantity"],
                "metadata": {},
            },
            "sk_test_offline",
        )

    monkeypatch.setattr(_APIRequestor, "request", request)

    item = stripe_client.modify_subscription_item(
        "si_offline", quantity=7, proration_behavior="create_prorations", idempotency_key="k1"
    )
    assert type(item) is dict and type(item["metadata"]) is dict
    assert item["quantity"] == 7
    assert sent[-1]["url"] == "/v1/subscription_items/si_offline"
    assert sent[-1]["options"]["idempotency_key"] == "k1"
    assert "idempotency_key" not in sent[-1]["params"]

    tenant = await _org(db)
    await _queue(db, tenant, seat_sync.seat_quantity(MANY))
    report = await seat_outbox.run(db, now=NOW)
    assert report.processed.sent >= 1
    (row,) = await _rows(db, tenant)
    assert row.succeeded_at is not None
    ours = [s for s in sent if s["url"].endswith(tenant.stripe_subscription_item_id)]
    assert ours[-1]["options"]["idempotency_key"] == row.payload["idempotency_key"]
    assert ours[-1]["params"] == {
        "quantity": seat_sync.seat_quantity(MANY),
        "proration_behavior": "create_prorations",
    }


def test_the_command_refuses_a_live_key_without_live(monkeypatch, capsys) -> None:
    from app.config import get_settings
    from scripts import stripe_seat_sync

    monkeypatch.setattr(get_settings(), "stripe_secret_key", "")
    assert stripe_seat_sync.main([]) == 2
    monkeypatch.setattr(get_settings(), "stripe_secret_key", "sk_live_x")
    assert stripe_seat_sync.main([]) == 2
    assert "--live" in capsys.readouterr().err
