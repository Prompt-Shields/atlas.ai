"""Create or update the Promptly enterprise catalogue in Stripe.

Applies ``app/services/billing/catalog.py``: the Work client, Atlas.AI,
Learn, Adopt, Suite and platform-fee products and prices, the
``guided_step`` meter and the step-overage price
(Prompt-Shields/promptly-guide#45, #46, #47).

Idempotent: products are found by id, the meter by event name and prices
by lookup key, so a second run reports every object as ``ok``.

Test mode by default: a live key (``sk_live_`` / ``rk_live_``) is refused
unless ``--live`` is passed.

Usage:
    STRIPE_SECRET_KEY=sk_test_... python -m scripts.stripe_catalog --dry-run
    STRIPE_SECRET_KEY=sk_test_... python -m scripts.stripe_catalog
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.services.billing import catalog


def _offline_plan() -> list[str]:
    """The plan against an empty account, for a dry run with no key."""
    lines = [f"create   product  {p.id} ({p.name})" for p in catalog.PRODUCTS]
    lines += [f"create   meter    {m.event_name} (sum per customer)" for m in catalog.METERS]
    lines += [
        f"create   price    {p.lookup_key} "
        f"({p.unit_amount / 100:.2f} {catalog.CURRENCY.upper()}/{p.interval})"
        for p in catalog.PRICES
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    parser.add_argument("--live", action="store_true", help="allow a live-mode key")
    args = parser.parse_args(argv)

    key = get_settings().stripe_secret_key
    if not key:
        if args.dry_run:
            print("STRIPE_SECRET_KEY is not set; plan against an empty account:")
            print("\n".join(_offline_plan()))
            return 0
        print("STRIPE_SECRET_KEY is not set.", file=sys.stderr)
        return 2
    if key.startswith(("sk_live_", "rk_live_")) and not args.live:
        print("Refusing a live-mode key without --live.", file=sys.stderr)
        return 2

    mode = "live" if "_live_" in key else "test"
    actions = catalog.sync_catalog(dry_run=args.dry_run)
    print(f"Stripe {mode} mode{' (dry run, nothing changed)' if args.dry_run else ''}:")
    for action in actions:
        print(action)
    return 0


if __name__ == "__main__":
    sys.exit(main())
