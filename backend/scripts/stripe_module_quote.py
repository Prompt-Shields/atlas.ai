"""Sell enterprise modules to a tenant by Stripe Quote.

Quotes are a sales action, so this is an operator script rather than an
endpoint. Run it from backend/ with the venv and STRIPE_SECRET_KEY set:

    # 1. Draft a quote for 250 seats of Atlas.AI and Learn.
    .venv/bin/python -m scripts.stripe_module_quote create \
        --tenant acme --modules atlas,learn --seats 250 \
        --billing-email ap@acme.example

    # 2. Finalise it; send the PDF from the Stripe dashboard.
    .venv/bin/python -m scripts.stripe_module_quote finalize qt_123

    # 3. Once the buyer signs, accept it. This starts the subscription,
    #    invoiced with 30-day terms and paid by EU bank transfer.
    .venv/bin/python -m scripts.stripe_module_quote accept qt_123

The platform fee is added automatically on the tenant's first paid module.
Use --platform-fee or --no-platform-fee to override.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from app.database import get_standalone_session
from app.models.tenant import Tenant
from app.services.billing import quotes


async def _create(args: argparse.Namespace) -> None:
    async with get_standalone_session() as db:
        try:
            cond = Tenant.id == uuid.UUID(args.tenant)
        except ValueError:
            cond = Tenant.slug == args.tenant
        tenant = await db.scalar(select(Tenant).where(cond))
        if tenant is None:
            raise quotes.QuoteError(f"No tenant with id or slug {args.tenant!r}")
        quote = await quotes.create_module_quote(
            db,
            tenant,
            modules=args.modules.split(","),
            seats=args.seats,
            billing_email=args.billing_email,
            include_platform_fee=args.platform_fee,
        )
    print(f"Draft quote {quote['id']} for tenant {tenant.slug}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Draft a quote for a tenant")
    create.add_argument("--tenant", required=True, help="Tenant id or slug")
    create.add_argument(
        "--modules",
        required=True,
        help=f"Comma-separated: {', '.join(quotes.MODULE_LOOKUP_KEYS)}",
    )
    create.add_argument("--seats", type=int, required=True, help="Licensed users")
    create.add_argument(
        "--billing-email", help="Invoice email, needed only if the tenant has no Stripe customer"
    )
    fee = create.add_mutually_exclusive_group()
    fee.add_argument("--platform-fee", dest="platform_fee", action="store_true", default=None)
    fee.add_argument("--no-platform-fee", dest="platform_fee", action="store_false")

    finalize = sub.add_parser("finalize", help="Finalise a draft quote")
    finalize.add_argument("quote_id")

    accept = sub.add_parser("accept", help="Accept a signed quote")
    accept.add_argument("quote_id")
    accept.add_argument(
        "--bank-country", help="eu_bank_transfer country; defaults to the configured one"
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            asyncio.run(_create(args))
        elif args.command == "finalize":
            quote = quotes.finalize_quote(args.quote_id)
            print(f"Quote {quote['id']} is {quote['status']}")
        else:
            quote = quotes.accept_quote(args.quote_id, bank_transfer_country=args.bank_country)
            print(f"Quote {quote['id']} accepted; subscription {quote['subscription']}")
    except quotes.QuoteError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
