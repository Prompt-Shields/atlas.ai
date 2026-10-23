"""Post each organisation's guided-step overage to Stripe's `guided_step` meter.

The hourly job of promptly-guide#62 (``docs/step-metering.md``). For every
organisation with a Stripe customer it queues the Work client overage not yet
posted, this month's and last month's, in ``billing_outbox``, then sends the
due rows as meter events (``app/services/billing/step_overage.py``). Each
increment has its own identifier and idempotency key, so running it twice, or
retrying after a failure, never bills a step twice. Run it hourly; it is
safe to run more often.

``--dry-run`` reads the database and prints what would be posted; it queues
nothing and does not call Stripe. A live key (``sk_live_`` / ``rk_live_``) is
refused unless ``--live`` is passed. A restricted key that may write meter
events, and nothing else, is enough.

Usage:
    python -m scripts.stripe_step_meter --dry-run
    STRIPE_SECRET_KEY=rk_live_... python -m scripts.stripe_step_meter --live
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import get_settings
from app.services.billing import step_overage


async def _run(*, send: bool) -> step_overage.RunReport:
    from app.database import get_session_factory

    async with get_session_factory()() as db:
        return await step_overage.run(db, send=send)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be posted; queue nothing and do not call Stripe",
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
    for inc in report.queued:
        print(f"{inc.tenant_id} {inc.period} +{inc.value} steps (month total {inc.through})")
    p = report.processed
    verb = "would queue" if args.dry_run else "queued"
    print(
        f"{verb} {len(report.queued)}; sent {p.sent}, retrying {p.retrying}, "
        f"dead-lettered {p.dead_lettered}; organisations failed {len(report.failed_tenants)}.",
        file=sys.stderr,
    )
    return 1 if p.dead_lettered or report.failed_tenants else 0


if __name__ == "__main__":
    sys.exit(main())
