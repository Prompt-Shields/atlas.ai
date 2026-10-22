"""Create or update the Stripe customer portal configurations.

Applies ``app/services/billing/portal.py`` (Prompt-Shields/promptly-guide#64):
- an enterprise portal with invoices and payment details only;
- a Plus portal with plan switching, payment method and cancellation.

Idempotent: each configuration is found by its ``promptly_portal``
metadata, so a second run reports both as ``ok``. Set the printed ids as
``STRIPE_PORTAL_CONFIGURATION_ENTERPRISE`` and
``STRIPE_PORTAL_CONFIGURATION_PLUS``.

The Plus plan switch offers the prices in ``STRIPE_PRICE_PLUS_MONTHLY`` and
``STRIPE_PRICE_PLUS_YEARLY``. Without them, Plus subscribers can still cancel
but not switch. No amounts are read or written.

Test mode by default: a live key (``sk_live_`` / ``rk_live_``) is refused
unless ``--live`` is passed.

Usage:
    STRIPE_SECRET_KEY=sk_test_... python -m scripts.stripe_portal --dry-run
    STRIPE_SECRET_KEY=sk_test_... python -m scripts.stripe_portal
"""

from __future__ import annotations

import argparse
import sys

from app.config import get_settings
from app.services.billing import portal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    parser.add_argument("--live", action="store_true", help="allow a live-mode key")
    args = parser.parse_args(argv)

    settings = get_settings()
    plus_prices = [settings.stripe_price_plus_monthly, settings.stripe_price_plus_yearly]
    if not any(plus_prices):
        print(
            "note: STRIPE_PRICE_PLUS_MONTHLY / _YEARLY are not set; "
            "the Plus portal will not offer a plan switch.",
            file=sys.stderr,
        )

    key = settings.stripe_secret_key
    if not key:
        if args.dry_run:
            print("STRIPE_SECRET_KEY is not set; plan against an empty account:")
            for action in portal.sync_portal_configurations(plus_prices, offline=True):
                print(action)
            return 0
        print("STRIPE_SECRET_KEY is not set.", file=sys.stderr)
        return 2
    if key.startswith(("sk_live_", "rk_live_")) and not args.live:
        print("Refusing a live-mode key without --live.", file=sys.stderr)
        return 2

    mode = "live" if "_live_" in key else "test"
    actions = portal.sync_portal_configurations(plus_prices, dry_run=args.dry_run)
    print(f"Stripe {mode} mode{' (dry run, nothing changed)' if args.dry_run else ''}:")
    for action in actions:
        print(action)
    for action in actions:
        if action.configuration_id:
            print(f"{portal.SETTING_FOR_KIND[action.kind]}={action.configuration_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
