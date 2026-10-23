"""Put an enterprise on Stripe and change its modules.

``app/services/billing/enterprise.py`` holds the enterprise subscription
(Prompt-Shields/promptly-guide#45, #46, #47): the free Work client with
quantity = provisioned users, paid modules as items on it, and the platform
fee with the first paid module unless the customer is a pilot. This script is
its operator entry point, as ``stripe_module_quote.py`` is for quotes. It also
attaches a business's VAT number for Stripe Tax (promptly-guide#50), which
invoiced customers never enter in Checkout.

Run it from backend/ with the venv and STRIPE_SECRET_KEY set:

    # Day one: the free Work client for 250 provisioned users.
    .venv/bin/python -m scripts.stripe_enterprise start \
        --tenant acme --users 250 --billing-email ap@acme.example [--pilot]

    # The VAT number Stripe Tax needs for reverse charge.
    .venv/bin/python -m scripts.stripe_enterprise vat-id --tenant acme --vat NO923456783MVA

    # Modules are items on that subscription.
    .venv/bin/python -m scripts.stripe_enterprise add-module --tenant acme --module learn --users 250
    .venv/bin/python -m scripts.stripe_enterprise remove-module --tenant acme --module learn
    .venv/bin/python -m scripts.stripe_enterprise end-pilot --tenant acme

Running ``start`` again only updates the quantity. No amounts are read or
printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_standalone_session
from app.models.tenant import Tenant
from app.services.billing import catalog, enterprise, tax


class CliError(Exception):
    """A problem to print and exit 1 on."""


async def _tenant(db: AsyncSession, ref: str) -> Tenant:
    try:
        cond = Tenant.id == uuid.UUID(ref)
    except ValueError:
        cond = Tenant.slug == ref
    tenant = await db.scalar(select(Tenant).where(cond))
    if tenant is None:
        raise CliError(f"No tenant with id or slug {ref!r}")
    return tenant


async def _run(args: argparse.Namespace) -> str:
    async with get_standalone_session() as db:
        tenant = await _tenant(db, args.tenant)
        if args.command == "start":
            sub = await enterprise.start_work_client(
                db,
                tenant,
                provisioned_users=args.users,
                pilot=args.pilot,
                billing_email=args.billing_email,
            )
            return f"Work client subscription {sub['id']} for tenant {tenant.slug}"
        if args.command == "add-module":
            sub = enterprise.add_module(tenant, args.module, licensed_users=args.users)
            return f"{args.module} on subscription {sub['id']} for tenant {tenant.slug}"
        if args.command == "remove-module":
            sub = enterprise.remove_module(tenant, args.module)
            return f"{args.module} off subscription {sub['id']} for tenant {tenant.slug}"
        if args.command == "end-pilot":
            sub = enterprise.end_pilot(tenant)
            return f"Pilot ended on subscription {sub['id']} for tenant {tenant.slug}"
        # vat-id
        if not tenant.stripe_customer_id:
            raise CliError(f"Tenant {tenant.slug} has no Stripe customer; run start first")
        tax_id = tax.attach_vat_id(tenant.stripe_customer_id, args.vat)
        return f"VAT ID {tax_id['type']} {tax_id['value']} on tenant {tenant.slug}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    modules = sorted(catalog.MODULE_PRICES)

    start = sub.add_parser("start", help="Start or update the free Work client subscription")
    start.add_argument("--tenant", required=True, help="Tenant id or slug")
    start.add_argument("--users", type=int, required=True, help="Provisioned users")
    start.add_argument("--pilot", action="store_true", help="Waive the platform fee")
    start.add_argument(
        "--billing-email", help="Invoice email, needed only if the tenant has no Stripe customer"
    )

    add = sub.add_parser("add-module", help="Add a paid module or set its quantity")
    add.add_argument("--tenant", required=True, help="Tenant id or slug")
    add.add_argument("--module", required=True, choices=modules)
    add.add_argument("--users", type=int, required=True, help="Licensed users")

    remove = sub.add_parser("remove-module", help="Remove a paid module")
    remove.add_argument("--tenant", required=True, help="Tenant id or slug")
    remove.add_argument("--module", required=True, choices=modules)

    end = sub.add_parser("end-pilot", help="Mark a pilot as converted")
    end.add_argument("--tenant", required=True, help="Tenant id or slug")

    vat = sub.add_parser("vat-id", help="Attach a Norwegian or EU VAT number")
    vat.add_argument("--tenant", required=True, help="Tenant id or slug")
    vat.add_argument("--vat", required=True, help="For example NO923456783MVA or DE123456789")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        print(asyncio.run(_run(args)))
    except (CliError, enterprise.EnterpriseBillingError, ValueError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
