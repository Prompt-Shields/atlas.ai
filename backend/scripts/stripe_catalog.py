"""Create or update the Promptly enterprise catalogue in Stripe.

Applies ``app/services/billing/catalog.py``: the Work client, Atlas.AI,
Learn, Adopt, Suite and platform-fee products and prices, the
``guided_step`` meter and the step-overage price
(Prompt-Shields/promptly-guide#45, #46, #47).

Idempotent: products are found by id, the meter by event name and prices
by lookup key, so a second run reports every object as ``ok``.

Test mode by default: a live key (``sk_live_`` / ``rk_live_``) is refused
unless ``--live`` is passed.

Amounts are not in the repository (it is public, and CONTRIBUTING.md forbids
committing pricing). Pass a JSON file of ``{lookup_key: amount in cents}``
with ``--amounts`` or ``STRIPE_CATALOG_AMOUNTS_FILE``; the script refuses to
run without one. Keep that file out of git.

Usage:
    STRIPE_SECRET_KEY=sk_test_... python -m scripts.stripe_catalog \
        --amounts ~/promptly-amounts.json --dry-run
    STRIPE_SECRET_KEY=sk_test_... python -m scripts.stripe_catalog \
        --amounts ~/promptly-amounts.json
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.services.billing import catalog


def _offline_plan(amounts: dict[str, int]) -> list[str]:
    """The plan against an empty account, for a dry run with no key."""
    lines = [f"create   product  {p.id} ({p.name})" for p in catalog.PRODUCTS]
    lines += [f"create   meter    {m.event_name} (sum per customer)" for m in catalog.METERS]
    lines += [
        f"create   price    {p.lookup_key} "
        f"({catalog.format_amount(amounts[p.lookup_key], p.interval)})"
        for p in catalog.PRICES
    ]
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    parser.add_argument("--live", action="store_true", help="allow a live-mode key")
    parser.add_argument(
        "--amounts",
        help="JSON file of {lookup_key: cents}, kept outside the repository "
        "(default: STRIPE_CATALOG_AMOUNTS_FILE)",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    amounts_file = args.amounts or settings.stripe_catalog_amounts_file
    if not amounts_file:
        print(
            "No amounts file. Pass --amounts or set STRIPE_CATALOG_AMOUNTS_FILE; "
            "prices are not kept in the repository.",
            file=sys.stderr,
        )
        return 2
    try:
        amounts = catalog.load_amounts(amounts_file)
    except catalog.AmountsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    key = settings.stripe_secret_key
    if not key:
        if args.dry_run:
            print("STRIPE_SECRET_KEY is not set; plan against an empty account:")
            print("\n".join(_offline_plan(amounts)))
            return 0
        print("STRIPE_SECRET_KEY is not set.", file=sys.stderr)
        return 2
    if key.startswith(("sk_live_", "rk_live_")) and not args.live:
        print("Refusing a live-mode key without --live.", file=sys.stderr)
        return 2

    mode = "live" if "_live_" in key else "test"
    actions = catalog.sync_catalog(amounts, dry_run=args.dry_run)
    print(f"Stripe {mode} mode{' (dry run, nothing changed)' if args.dry_run else ''}:")
    for action in actions:
        print(action)
    return 0


if __name__ == "__main__":
    sys.exit(main())
