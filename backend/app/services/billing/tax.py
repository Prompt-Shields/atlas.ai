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

# EU VAT number formats after the country prefix, as published for VIES.
# Greece uses EL, not GR, and Northern Ireland traders keep an XI number for
# goods; both are `eu_vat` in Stripe. Only the shape is checked here: Stripe
# checks EU numbers against VIES itself, after the fact.
_EU_VAT_FORMATS = {
    "AT": r"U\d{8}",
    "BE": r"[01]\d{9}",
    "BG": r"\d{9,10}",
    "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}",
    "DE": r"\d{9}",
    "DK": r"\d{8}",
    "EE": r"\d{9}",
    "EL": r"\d{9}",
    "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",
    "FI": r"\d{8}",
    "FR": r"[A-HJ-NP-Z0-9]{2}\d{9}",
    "HR": r"\d{11}",
    "HU": r"\d{8}",
    "IE": r"\d{7}[A-W][A-IW]?|\d[A-Z+*]\d{5}[A-W]",
    "IT": r"\d{11}",
    "LT": r"\d{9}|\d{12}",
    "LU": r"\d{8}",
    "LV": r"\d{11}",
    "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}",
    "PL": r"\d{10}",
    "PT": r"\d{9}",
    "RO": r"[1-9]\d{1,9}",
    "SE": r"\d{10}01",
    "SI": r"\d{8}",
    "SK": r"\d{10}",
    "XI": r"\d{9}|\d{12}|GD\d{3}|HA\d{3}",
}
EU_VAT_PREFIXES = frozenset(_EU_VAT_FORMATS)
_EU_VAT = {cc: re.compile(rf"(?:{fmt})", re.ASCII) for cc, fmt in _EU_VAT_FORMATS.items()}

# Buyers often write their ISO country code; VIES and Stripe want EL for Greece.
_PREFIX_ALIASES = {"GR": "EL"}

# Norwegian VAT number as Stripe wants it: nine-digit organisation number
# followed by "MVA". Buyers often write "NO" in front, so we accept that.
_NO_VAT = re.compile(r"(?:NO)?([89]\d{8})MVA", re.ASCII)
_NO_ORG_WEIGHTS = (3, 2, 7, 6, 5, 4, 3, 2)


def _valid_no_org_number(digits: str) -> bool:
    """MOD11 check digit of a Norwegian organisation number (Brønnøysund)."""
    total = sum(int(d) * w for d, w in zip(digits[:8], _NO_ORG_WEIGHTS, strict=True))
    check = 11 - total % 11
    if check == 11:
        check = 0
    return check != 10 and check == int(digits[8])


def automatic_tax_params(enabled: bool) -> dict:
    """The ``automatic_tax`` param for a Stripe create or update call.

    ``enabled`` is `Settings.stripe_automatic_tax`. Every call that creates
    or changes a subscription or quote takes its Stripe Tax setting from
    here, so they all follow the one flag:

    * Checkout sessions (Team and Plus), through ``checkout_tax_params``;
    * module quotes (``quotes.create_module_quote``), and the subscription a
      quote creates when ``quotes.accept_quote`` sets it up;
    * the enterprise Work client subscription (``enterprise``), when it is
      created and on every change to its items or metadata.

    Off sends nothing rather than ``enabled: false``. Off exists for test
    accounts without a head-office address, which cannot turn Stripe Tax on
    at all, so nothing there has it to turn off; and leaving the param out
    keeps an update call from changing what a subscription already has.
    """
    return {"automatic_tax": {"enabled": True}} if enabled else {}


def checkout_tax_params(*, enabled: bool, business: bool) -> dict:
    """Extra `checkout.Session.create` params for Stripe Tax.

    In subscription mode the session's ``automatic_tax`` carries over to the
    subscription it creates and to that subscription's invoices. Checkout is
    one of three ways Atlas creates a subscription: module quotes and the
    enterprise Work client subscription are the others, and they take the
    same setting from ``automatic_tax_params``.

    ``business`` adds VAT ID collection and a required billing address: the
    VAT ID is what lets Stripe apply reverse charge, and an invoice to a
    business should carry its address. Neither needs Stripe Tax, so they do
    not wait for ``enabled``; VAT IDs collected now are in place when it is
    switched on.

    All three params are valid in both ``payment`` and ``subscription``
    mode. Plus checkout passes ``customer_email``. Team checkout passes the
    tenant's existing ``customer`` when it has one, and a caller that does
    must also send ``customer_update={"address": "auto", "name": "auto"}``,
    or Stripe rejects ``automatic_tax`` and ``tax_id_collection``; see
    ``routers.billing.checkout_customer_params``.
    """
    params = automatic_tax_params(enabled)
    if business:
        params["tax_id_collection"] = {"enabled": True}
        params["billing_address_collection"] = "required"
    return params


def tax_id_for(vat_id: str) -> tuple[str, str]:
    """Return Stripe's ``(type, value)`` for a Norwegian or EU VAT number.

    Spaces, dots and dashes are dropped and letters upper-cased. A Norwegian
    number must carry "MVA" and a valid organisation-number check digit; an
    EU number must match its country's format, with ``GR`` read as ``EL``.
    Stripe checks EU numbers against VIES itself, after the fact, and reports
    the result on the tax ID's ``verification.status``.
    """
    value = re.sub(r"[\s.\-]", "", vat_id).upper()
    no = _NO_VAT.fullmatch(value)
    if no and _valid_no_org_number(no.group(1)):
        return "no_vat", f"{no.group(1)}MVA"
    prefix = _PREFIX_ALIASES.get(value[:2], value[:2])
    fmt = _EU_VAT.get(prefix)
    if fmt is not None and fmt.fullmatch(value[2:]):
        return "eu_vat", prefix + value[2:]
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
