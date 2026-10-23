"""Send the seat syncs queued in the billing outbox to Stripe.

When Stripe is unreachable at a user's first login, ``seat_sync`` queues a
``sync_seat`` row in ``billing_outbox``. For every organisation with a
subscription item, this sends the newest queued row with the organisation's
seat count as it is now, settles older rows as superseded, and retries or
dead-letters failures as the outbox does (``app/services/billing/seat_outbox.py``).
Each row's request has its own idempotency key, so running it twice, or
retrying after a failure, never changes a subscription twice. Run it hourly;
it is safe to run more often.

``--dry-run`` reads the database and prints what would be sent; it changes
nothing and does not call Stripe. A live key (``sk_live_`` / ``rk_live_``) is
refused unless ``--live`` is passed. A restricted key that may write
subscriptions is enough.

Usage:
    python -m scripts.stripe_seat_sync --dry-run
    STRIPE_SECRET_KEY=rk_live_... python -m scripts.stripe_seat_sync --live
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import get_settings
from app.services.billing import seat_outbox


async def _run(*, send: bool) -> seat_outbox.RunReport:
    from app.database import get_session_factory

    async with get_session_factory()() as db:
        return await seat_outbox.run(db, send=send)


def _describe(p: seat_outbox.Planned) -> str:
    older = f"; {p.superseded} older superseded" if p.superseded else ""
    if p.skipped:
        what = "no active subscription, skipped"
    elif not p.due:
        what = "not due yet"
    else:
        what = f"{p.quantity} seats"
    return f"{p.tenant_id} row {p.outbox_id}: {what}{older}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be sent; change nothing and do not call Stripe",
    )
    parser.add_argument("--live", action="store_true", help="allow a live-mode key")
    args = parser.parse_args(argv)

    key = get_settings().stripe_secret_key
    if not args.dry_run:
        if not key:
            print("STRIPE_SECRET_KEY is not set.", file=sys.stderr)
            return 2
        if key.startswith(("sk_live_", "rk_live_")) and not args.live:
            print("Refusing a live-mode key without --live.", file=sys.stderr)
            return 2

    report = asyncio.run(_run(send=not args.dry_run))
    for planned in report.planned:
        print(_describe(planned))
    p = report.processed
    verb = "would send" if args.dry_run else "due"
    due = sum(1 for x in report.planned if x.quantity is not None)
    print(
        f"{verb} {due}; sent {p.sent}, retrying {p.retrying}, "
        f"dead-lettered {p.dead_lettered}; organisations failed {len(report.failed_tenants)}.",
        file=sys.stderr,
    )
    return 1 if p.dead_lettered or report.failed_tenants else 0


if __name__ == "__main__":
    sys.exit(main())
