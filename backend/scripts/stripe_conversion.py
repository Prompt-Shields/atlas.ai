"""Print the conversion report joined to Stripe's dates, as CSV.

The read-only Stripe pass of promptly-guide#43 (``docs/conversion-metrics.md``,
decision 9). It builds the same rows as ``GET /api/v1/admin/conversion``, then
reads from Stripe, for each company with a Stripe customer, when its first
module was sold, whether it was quoted and paid, and its pilot flag
(``app/services/billing/conversion_stripe.py``). One CSV row per company goes
to standard output, for the finance folder. Keep the CSV there, not the inputs.

Read only: it lists subscriptions, invoices and quotes, and changes nothing in
Stripe. The database is read in a read-only transaction that is rolled back.
Nothing is stored. A restricted key that may read subscriptions, invoices,
quotes and prices, and nothing else, is enough.

Test mode by default: a live key (``sk_live_`` / ``rk_live_``) is refused
unless ``--live`` is passed. The monthly report needs live data, so it is run
with ``--live``; a test key reads test-mode objects only.

Usage:
    python -m scripts.stripe_conversion --dry-run
    STRIPE_SECRET_KEY=rk_live_... python -m scripts.stripe_conversion --live \
        > conversion-2026-10.csv
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import text

from app.config import get_settings
from app.services import conversion_report
from app.services.billing import conversion_stripe


async def load_companies() -> list[conversion_report.CompanyRow]:
    """The admin report's company rows, read in a read-only transaction."""
    from app.database import get_engine, get_session_factory

    async with get_session_factory()() as db:
        if get_engine().dialect.name == "postgresql":
            await db.execute(text("SET TRANSACTION READ ONLY"))
        try:
            return (await conversion_report.build_report(db)).companies
        finally:
            await db.rollback()


def _plan() -> list[str]:
    """What a run would read, without calling Stripe or the database."""
    return [
        "read     database  the rows of GET /api/v1/admin/conversion (read-only transaction)",
        *(
            f"read     stripe    {call}, per company with a customer"
            for call in conversion_stripe.READS
        ),
        "write    nothing",
        "print    CSV: " + ",".join(conversion_stripe.COLUMNS),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be read; call neither Stripe nor the database",
    )
    parser.add_argument("--live", action="store_true", help="allow a live-mode key")
    args = parser.parse_args(argv)

    key = get_settings().stripe_secret_key
    if args.dry_run:
        print("\n".join(_plan()))
        return 0
    if not key:
        print("STRIPE_SECRET_KEY is not set.", file=sys.stderr)
        return 2
    if key.startswith(("sk_live_", "rk_live_")) and not args.live:
        print("Refusing a live-mode key without --live.", file=sys.stderr)
        return 2

    mode = "live" if "_live_" in key else "test"
    companies = asyncio.run(load_companies())
    facts = conversion_stripe.read_all(companies)
    rows = conversion_stripe.join(companies, facts)
    sys.stdout.write(conversion_stripe.to_csv(rows))
    partial = sum(bool(f.warnings) for f in facts.values())
    print(
        f"Stripe {mode} mode: {len(rows)} companies, {len(facts)} customers read"
        + (f", {partial} partly (see the stripe column)" if partial else "")
        + ". Nothing was changed.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
