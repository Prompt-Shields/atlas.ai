"""Post the Work client's guided-step overage to Stripe (promptly-guide #62).

promptly-guide `docs/step-metering.md`: each hour, work out how far each
organisation's month is above its pool and post that excess to the
`guided_step` meter, through the billing outbox, so that a Stripe outage
retries rather than loses steps and a retry never bills twice.

The meter sums its events per customer (``catalog._sync_meter``), so what is
posted is the *increase* since the last post, not the month's total:

  * ``enqueue_overage`` reads ``guided_step_meter.pool_status`` for the month
    and compares its ``overage`` with the highest total already queued for
    that organisation and month (``through``). If the overage is higher, it
    queues one ``billing_outbox`` row for the difference.
  * ``process_due`` sends due rows with ``stripe_client.create_meter_event``.

**One identifier per increment.** A row's meter event identifier, and its
idempotency key, is the organisation, the month and the total it brings the
month to: ``guided_step:<tenant>:<YYYY-MM>:<through>``. Totals only rise, so
each increment has its own identifier, and the same increment always has the
same one: two runs that queue it twice, or a retry after a lost response,
send Stripe the same identifier, which it counts once.

Only hosted steps are ever counted (`guided_step_meter`), and organisations
with their own endpoint have no overage, so they never reach Stripe.

What leaves Atlas: the organisation's Stripe customer id and a number of
steps. Nothing per person (E4).

Months are UTC calendar months. ``now`` MUST come from the DB server's clock,
as for ``guided_step_meter``; ``run`` reads it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_guc
from app.models.billing import BillingOutbox
from app.models.tenant import Tenant
from app.services import guided_step_meter
from app.services.billing import outbox, stripe_client
from app.services.billing.catalog import GUIDED_STEP_EVENT

log = structlog.get_logger()

OPERATION = "guided_step_overage"

# The retry rules are the outbox's (``outbox.RETRY_WINDOW``): Stripe keeps an
# idempotency key for 24 hours and enforces a meter event identifier's
# uniqueness for at least 24 hours, and a row still failing after this long is
# dead-lettered rather than retried past that point.
RETRY_WINDOW = outbox.RETRY_WINDOW
MAX_BACKOFF = outbox.MAX_BACKOFF
BATCH = outbox.BATCH
Processed = outbox.Processed
db_now = outbox.db_now
_is_postgres = outbox.is_postgres


def identifier(tenant_id: uuid.UUID, period: str, through: int) -> str:
    """The meter event identifier (and idempotency key) of one increment."""
    return f"{GUIDED_STEP_EVENT}:{tenant_id}:{period}:{through}"


def month_start(at: datetime) -> datetime:
    at = at.astimezone(UTC)
    return datetime(at.year, at.month, 1, tzinfo=UTC)


async def posted_through(db: AsyncSession, tenant_id: uuid.UUID, period: str) -> int:
    """The highest month total already queued for this organisation and month.

    Every row counts, sent, pending or dead-lettered: a dead-lettered
    increment may still have reached Stripe, so it is never queued again
    under a new identifier. It is left for a person (see ``process_due``).
    """
    through = await db.scalar(
        select(func.max(BillingOutbox.payload["through"].as_integer())).where(
            BillingOutbox.tenant_id == tenant_id,
            BillingOutbox.operation == OPERATION,
            BillingOutbox.payload["period"].as_string() == period,
        )
    )
    return through or 0


@dataclass(frozen=True)
class Increment:
    tenant_id: uuid.UUID
    period: str
    through: int  # the month's overage once this is posted
    value: int  # steps this event adds


async def enqueue_overage(db: AsyncSession, tenant: Tenant, *, at: datetime) -> Increment | None:
    """Queue the month's unposted overage, as of ``at``, if there is any.

    ``at`` is both the moment the month's count is read and the meter event's
    timestamp, so the event lands in the month it was counted in. If the
    overage has fallen (more seats this month) nothing is queued: what was
    posted stays posted, and only a rise above the highest total is new.
    """
    status = await guided_step_meter.pool_status(db, tenant, now=at)
    posted = await posted_through(db, tenant.id, status.period)
    if status.overage <= posted:
        return None
    if not tenant.stripe_customer_id:
        log.warning(
            "guided_step_overage_no_customer",
            tenant_id=str(tenant.id),
            period=status.period,
            unposted=status.overage - posted,
        )
        return None

    inc = Increment(tenant.id, status.period, status.overage, status.overage - posted)
    db.add(
        BillingOutbox(
            tenant_id=tenant.id,
            operation=OPERATION,
            payload={
                "period": inc.period,
                "through": inc.through,
                "value": inc.value,
                "customer": tenant.stripe_customer_id,
                "timestamp": int(at.timestamp()),
                "identifier": identifier(tenant.id, inc.period, inc.through),
            },
            next_attempt_at=at,
        )
    )
    await db.flush()
    return inc


async def enqueue_months(db: AsyncSession, tenant: Tenant, *, now: datetime) -> list[Increment]:
    """Queue this month's overage and settle last month's.

    Steps recorded after the last hourly run of a month are still that
    month's: the first runs of the next month post them, timestamped at the
    last second of the month they were served in.
    """
    last_second = month_start(now) - timedelta(seconds=1)
    out = []
    for at in (last_second, now):
        inc = await enqueue_overage(db, tenant, at=at)
        if inc is not None:
            out.append(inc)
    return out


async def process_due(
    db: AsyncSession, tenant_id: uuid.UUID, *, now: datetime, limit: int = BATCH
) -> Processed:
    """Send this organisation's due overage rows to the meter. The caller commits.

    * Sent: ``succeeded_at`` is set.
    * ``InvalidRequestError`` (a customer Stripe does not know, a timestamp
      out of range): dead-lettered at once; retrying cannot fix it.
    * Any other Stripe error: retried with backoff, then dead-lettered once
      the row has been failing for ``RETRY_WINDOW``.

    A dead-lettered row is logged as ``guided_step_overage_dead_letter`` and
    needs a person: its steps are not queued again (``posted_through``).
    """
    rows = await outbox.due_rows(db, tenant_id, OPERATION, now=now, limit=limit)

    def send(row: BillingOutbox) -> None:
        p = row.payload
        stripe_client.create_meter_event(
            event_name=GUIDED_STEP_EVENT,
            payload={"stripe_customer_id": p["customer"], "value": str(p["value"])},
            identifier=p["identifier"],
            timestamp=p["timestamp"],
            idempotency_key=p["identifier"],
        )

    result = outbox.attempt(rows, now=now, send=send, on_dead_letter=_log_dead_letter)
    await db.flush()
    return result


def _log_dead_letter(row: BillingOutbox) -> None:
    log.error(
        "guided_step_overage_dead_letter",
        tenant_id=str(row.tenant_id),
        outbox_id=row.id,
        period=row.payload.get("period"),
        through=row.payload.get("through"),
        value=row.payload.get("value"),
        error=row.last_error,
    )


@dataclass
class RunReport:
    queued: list[Increment] = field(default_factory=list)
    processed: Processed = field(default_factory=Processed)
    failed_tenants: list[uuid.UUID] = field(default_factory=list)


async def run(db: AsyncSession, *, now: datetime | None = None, send: bool = True) -> RunReport:
    """The hourly pass over every organisation with a Stripe customer.

    One transaction per organisation, with its RLS tenant set, so one
    organisation's failure never holds up another's. ``send=False`` queues
    nothing for good: each organisation's work is rolled back and Stripe is
    not called, so the report says what a run would post.
    """
    report = RunReport()
    tenant_ids = (
        (await db.execute(select(Tenant.id).where(Tenant.stripe_customer_id.is_not(None))))
        .scalars()
        .all()
    )
    await db.commit()
    for tid in tenant_ids:
        try:
            at = now or await db_now(db)
            await set_tenant_guc(db, tid)
            lock = select(Tenant).where(Tenant.id == tid).execution_options(populate_existing=True)
            if _is_postgres(db):
                # Serialises two runs over the same organisation.
                lock = lock.with_for_update()
            tenant = (await db.execute(lock)).scalar_one_or_none()
            if tenant is None:
                await db.rollback()
                continue
            report.queued.extend(await enqueue_months(db, tenant, now=at))
            if not send:
                await db.rollback()
                continue
            report.processed.add(await process_due(db, tid, now=at))
            await db.commit()
        except Exception:
            await db.rollback()
            report.failed_tenants.append(tid)
            log.exception("guided_step_overage_tenant_failed", tenant_id=str(tid))
    return report
