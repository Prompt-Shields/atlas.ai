"""Stripe Tax for business customers (promptly-guide#50).

The business plan asks for Norwegian VAT at 25% domestically and reverse
charge in the EU when the buyer has a valid VAT ID. We do not compute any of
that here. Stripe Tax decides the rate, the reverse charge and any exemption
from three inputs: the account's head-office address and registrations (set
in the dashboard), the customer's address, and the customer's tax IDs. So
this module only makes sure Stripe gets those inputs. It never sets
``tax_exempt`` or a tax rate by hand; doing so would go stale the moment a
registration changes.

Consumer VAT in other EU countries needs one-stop-shop registration, which
is owner decision promptly-guide#67 and not handled here. Until then Stripe
Tax charges consumers only where the account is registered.
"""

from __future__ import annotations

import re

from app.services.billing import stripe_client

# EU VAT number prefixes. Greece uses EL, not GR, and Northern Ireland
# traders keep an XI number for goods; both are `eu_vat` in Stripe.
EU_VAT_PREFIXES = frozenset(
    "AT BE BG CY CZ DE DK EE EL ES FI FR HR HU IE IT LT LU LV MT NL PL PT RO SE SI SK XI".split()
)

# Norwegian VAT number as Stripe wants it: nine-digit organisation number
# followed by "MVA". Buyers often write "NO" in front, so we accept that.
_NO_VAT = re.compile(r"^(?:NO)?(\d{9})MVA$")


def checkout_tax_params(*, enabled: bool, business: bool) -> dict:
    """Extra `checkout.Session.create` params for Stripe Tax.

    In subscription mode the session's ``automatic_tax`` carries over to the
    subscription it creates and to that subscription's invoices. Atlas
    creates subscriptions only through Checkout, so this covers them all.

    ``business`` adds VAT ID collection and a required billing address: the
    VAT ID is what lets Stripe apply reverse charge, and an invoice to a
    business should carry its address. Neither needs Stripe Tax, so they do
    not wait for ``enabled``; VAT IDs collected now are in place when it is
    switched on.
    """
    params: dict = {}
    if enabled:
        params["automatic_tax"] = {"enabled": True}
    if business:
        params["tax_id_collection"] = {"enabled": True}
        params["billing_address_collection"] = "required"
    return params


def tax_id_for(vat_id: str) -> tuple[str, str]:
    """Return Stripe's ``(type, value)`` for a Norwegian or EU VAT number.

    Only the shape is checked here. Stripe checks EU numbers against VIES
    itself, after the fact, and reports the result on the tax ID's
    ``verification.status``.
    """
    value = re.sub(r"[\s.\-]", "", vat_id).upper()
    no = _NO_VAT.match(value)
    if no:
        return "no_vat", f"{no.group(1)}MVA"
    if len(value) > 2 and value[:2] in EU_VAT_PREFIXES and value[2:].isalnum():
        return "eu_vat", value
    raise ValueError(f"not a Norwegian or EU VAT number: {vat_id!r}")


def attach_vat_id(customer_id: str, vat_id: str):
    """Attach a business's VAT number to its Stripe customer.

    For invoiced enterprises, which never pass through Checkout and so never
    see ``tax_id_collection``. Safe to call again with the same number: an
    existing match is returned rather than added twice.
    """
    if not customer_id:
        raise ValueError("customer_id is required")
    tax_type, value = tax_id_for(vat_id)
    existing = stripe_client.list_tax_ids(customer_id, limit=100)
    for tax_id in existing.get("data", []):
        if tax_id.get("type") == tax_type and tax_id.get("value") == value:
            return tax_id
    return stripe_client.create_tax_id(customer_id, type=tax_type, value=value)
