"""Send the seat syncs queued in the billing outbox to Stripe.

``seat_sync.sync_seat_count`` sets the Team subscription item's quantity when
a user first logs in. When Stripe is unreachable it queues a ``sync_seat``
row in ``billing_outbox`` instead. This module sends those rows.

**A seat sync sets a quantity; it does not add one.** So only the latest
figure matters, and an old row must never undo a newer one:

  * Only an organisation's newest unsettled ``sync_seat`` row is sent. Older
    unsettled rows are settled as superseded (``succeeded_at`` set,
    ``superseded_by`` in the payload) without calling Stripe.
  * The quantity sent is worked out when the row is sent, from the
    organisation's activated users now (``seat_sync.seat_quantity``), not
    taken from the row. A user who logged in after the row was queued, while
    it waited or between its retries, is counted. The row's own
    ``quantity`` is what was wanted when it was queued and is kept as it was.
  * An organisation with no active subscription any more (cancelled, or no
    subscription item) has nothing to sync: its rows are settled as skipped
    (``skipped`` in the payload), without calling Stripe.

**One idempotency key per row and request:**
``sync_seat:<tenant>:<row id>:<subscription item>:<quantity>``. A retry of
the same row with the same figure is the same request, so Stripe answers it
once. If the figure has changed between attempts the key changes with it,
because Stripe refuses a key reused with different parameters; setting a
quantity twice is harmless, so the new request is safe.

Retries, backoff and dead-lettering are the outbox's (``outbox.attempt``),
the same as for the guided-step overage. A dead-lettered row is logged as
``sync_seat_dead_letter`` and needs a person; the next user's first login
syncs the seat count again.

What leaves Atlas: the subscription item id and a number of seats.
``now`` MUST come from the DB server's clock; ``run`` reads it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import set_tenant_guc
from app.models.billing import BillingOutbox
from app.models.tenant import Tenant
from app.services.billing import outbox, seat_sync, stripe_client

log = structlog.get_logger()

OPERATION = seat_sync.OPERATION
PRORATION_BEHAVIOR = "create_prorations"  # as seat_sync.sync_seat_count


def idempotency_key(tenant_id: uuid.UUID, row_id: int, item: str, quantity: int) -> str:
    """The idempotency key of one row's request for one quantity."""
    return f"{OPERATION}:{tenant_id}:{row_id}:{item}:{quantity}"


@dataclass(frozen=True)
class Planned:
    """What one organisation's pass did, or would do on a dry run."""

    tenant_id: uuid.UUID
    outbox_id: int  # the newest row, the one sent
    superseded: int  # older rows settled without a call
    quantity: int | None = None  # None when nothing is sent this pass
    skipped: bool = False  # no active subscription: nothing to sync
    due: bool = True


@dataclass
class RunReport:
    planned: list[Planned] = field(default_factory=list)
    processed: outbox.Processed = field(default_factory=outbox.Processed)
    failed_tenants: list[uuid.UUID] = field(default_factory=list)


async def process_tenant(
    db: AsyncSession, tenant: Tenant, *, now: datetime, send: bool = True
) -> tuple[Planned | None, outbox.Processed]:
    """Settle this organisation's ``sync_seat`` rows. The caller commits.

    The caller has set the RLS tenant and locked the tenant row. With
    ``send=False`` Stripe is not called; the caller rolls back.
    """
    done = outbox.Processed()
    stmt = outbox.unsettled(tenant.id, OPERATION).order_by(BillingOutbox.id.desc())
    if outbox.is_postgres(db):
        stmt = stmt.with_for_update(skip_locked=True)
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return None, done

    newest, older = rows[0], rows[1:]
    for row in older:
        row.succeeded_at = now
        row.payload = {**row.payload, "superseded_by": newest.id}

    if not seat_sync.has_active_subscription(tenant):
        newest.succeeded_at = now
        newest.payload = {**newest.payload, "skipped": "no_active_subscription"}
        await db.flush()
        return Planned(tenant.id, newest.id, len(older), skipped=True), done

    if _utc(newest.next_attempt_at) > now:
        await db.flush()
        return Planned(tenant.id, newest.id, len(older), due=False), done

    item = tenant.stripe_subscription_item_id
    quantity = seat_sync.seat_quantity(await seat_sync.activated_users(db, tenant.id))
    planned = Planned(tenant.id, newest.id, len(older), quantity=quantity)
    if not send:
        return planned, done

    key = idempotency_key(tenant.id, newest.id, item, quantity)
    newest.payload = {
        **newest.payload,
        "sent_quantity": quantity,
        "item": item,
        "idempotency_key": key,
    }

    def call(row: BillingOutbox) -> None:
        stripe_client.modify_subscription_item(
            item,
            quantity=quantity,
            proration_behavior=PRORATION_BEHAVIOR,
            idempotency_key=key,
        )

    done = outbox.attempt([newest], now=now, send=call, on_dead_letter=_log_dead_letter)
    if done.sent:
        tenant.seats_billed = quantity
    await db.flush()
    return planned, done


def _utc(at: datetime) -> datetime:
    # SQLite hands back naive datetimes; Postgres aware ones.
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _log_dead_letter(row: BillingOutbox) -> None:
    log.error(
        "sync_seat_dead_letter",
        tenant_id=str(row.tenant_id),
        outbox_id=row.id,
        quantity=row.payload.get("sent_quantity"),
        error=row.last_error,
    )


async def run(db: AsyncSession, *, now: datetime | None = None, send: bool = True) -> RunReport:
    """One pass over every organisation with a subscription item.

    One transaction per organisation, with its RLS tenant set and its tenant
    row locked, so two runs handle one organisation in turn and one
    organisation's failure never holds up another's. ``send=False`` changes
    nothing: each organisation's work is rolled back and Stripe is not
    called, so the report says what a run would send.
    """
    report = RunReport()
    tenant_ids = (
        (await db.execute(select(Tenant.id).where(Tenant.stripe_subscription_item_id.is_not(None))))
        .scalars()
        .all()
    )
    await db.commit()
    for tid in tenant_ids:
        try:
            at = now or await outbox.db_now(db)
            await set_tenant_guc(db, tid)
            lock = select(Tenant).where(Tenant.id == tid).execution_options(populate_existing=True)
            if outbox.is_postgres(db):
                lock = lock.with_for_update()
            tenant = (await db.execute(lock)).scalar_one_or_none()
            if tenant is None:
                await db.rollback()
                continue
            planned, done = await process_tenant(db, tenant, now=at, send=send)
            if planned is not None:
                report.planned.append(planned)
            if not send:
                await db.rollback()
                continue
            report.processed.add(done)
            await db.commit()
        except Exception:
            await db.rollback()
            report.failed_tenants.append(tid)
            log.exception("sync_seat_tenant_failed", tenant_id=str(tid))
    return report
