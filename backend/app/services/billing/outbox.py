"""The billing outbox: Stripe calls queued for retry.

``enqueue`` queues a row. The rest is shared by the processors that send
rows to Stripe (``step_overage``, ``seat_outbox``): reading a tenant's due
rows and recording what happened to each attempt, with the same retry,
backoff and dead-letter rules for every operation.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import stripe
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.billing import BillingOutbox

# Stripe keeps an idempotency key for 24 hours (and a meter event identifier
# unique for at least 24 hours). A row still failing after this long is
# dead-lettered rather than retried past the point where Stripe could tell a
# retry from a new request.
RETRY_WINDOW = timedelta(hours=20)
MAX_BACKOFF = timedelta(hours=1)
BATCH = 100


async def enqueue(
    db: AsyncSession, *, operation: str, tenant_id: uuid.UUID, payload: dict | None = None
) -> None:
    db.add(BillingOutbox(tenant_id=tenant_id, operation=operation, payload=payload or {}))
    await db.flush()


def is_postgres(db: AsyncSession) -> bool:
    return db.get_bind().dialect.name == "postgresql"


async def db_now(db: AsyncSession) -> datetime:
    """The DB server's clock (UTC); the process clock off Postgres."""
    if is_postgres(db):
        return (await db.scalar(select(func.now()))).astimezone(UTC)
    return datetime.now(UTC)


def unsettled(tenant_id: uuid.UUID, operation: str):
    """A select of the tenant's rows of ``operation`` that are neither sent nor dead."""
    return select(BillingOutbox).where(
        BillingOutbox.tenant_id == tenant_id,
        BillingOutbox.operation == operation,
        BillingOutbox.succeeded_at.is_(None),
        BillingOutbox.dead_lettered_at.is_(None),
    )


async def due_rows(
    db: AsyncSession, tenant_id: uuid.UUID, operation: str, *, now: datetime, limit: int = BATCH
) -> Sequence[BillingOutbox]:
    """The tenant's due rows of ``operation``, oldest first, locked on Postgres
    (``SKIP LOCKED``, so a second run passes over rows the first holds)."""
    stmt = (
        unsettled(tenant_id, operation)
        .where(BillingOutbox.next_attempt_at <= now)
        .order_by(BillingOutbox.id)
        .limit(limit)
    )
    if is_postgres(db):
        stmt = stmt.with_for_update(skip_locked=True)
    return (await db.execute(stmt)).scalars().all()


def backoff(attempts: int) -> timedelta:
    return min(timedelta(minutes=2 ** min(attempts, 10)), MAX_BACKOFF)


def describe(exc: stripe.StripeError) -> str:
    return f"{type(exc).__name__}: {exc.user_message or exc}"[:500]


@dataclass
class Processed:
    sent: int = 0
    retrying: int = 0
    dead_lettered: int = 0
    errors: list[str] = field(default_factory=list)

    def add(self, other: Processed) -> None:
        self.sent += other.sent
        self.retrying += other.retrying
        self.dead_lettered += other.dead_lettered
        self.errors.extend(other.errors)


def attempt(
    rows: Sequence[BillingOutbox],
    *,
    now: datetime,
    send: Callable[[BillingOutbox], object],
    on_dead_letter: Callable[[BillingOutbox], None],
) -> Processed:
    """Send each row with ``send`` and record the outcome on it. The caller flushes.

    * Sent: ``succeeded_at`` is set.
    * ``InvalidRequestError`` (a customer or item Stripe does not know, a
      value out of range): dead-lettered at once; retrying cannot fix it.
    * Any other Stripe error: retried with backoff, then dead-lettered once
      the row has been failing for ``RETRY_WINDOW``.

    ``on_dead_letter`` is called for each row as it is dead-lettered, to log it.
    """
    result = Processed()
    for row in rows:
        row.attempt_count += 1
        try:
            send(row)
        except stripe.InvalidRequestError as exc:
            _dead_letter(row, now, exc)
            on_dead_letter(row)
            result.dead_lettered += 1
            result.errors.append(row.last_error)
        except stripe.StripeError as exc:
            p = row.payload
            first_failed = p.get("first_failed_at")
            if first_failed is None:
                row.payload = {**p, "first_failed_at": int(now.timestamp())}
                first_failed = int(now.timestamp())
            if now - datetime.fromtimestamp(first_failed, UTC) >= RETRY_WINDOW:
                _dead_letter(row, now, exc)
                on_dead_letter(row)
                result.dead_lettered += 1
            else:
                row.last_error = describe(exc)
                row.next_attempt_at = now + backoff(row.attempt_count)
                result.retrying += 1
            result.errors.append(row.last_error)
        else:
            row.succeeded_at = now
            row.last_error = None
            result.sent += 1
    return result


def _dead_letter(row: BillingOutbox, now: datetime, exc: stripe.StripeError) -> None:
    row.dead_lettered_at = now
    row.last_error = describe(exc)
