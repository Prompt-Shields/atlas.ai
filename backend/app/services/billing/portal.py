"""Stripe customer portal: one configuration per kind of customer (promptly-guide#64).

The business plan asks for two portals:
- **Enterprise** customers see invoices and payment details only. Seats are
  provisioned users, changed in the admin console and synced to Stripe, and
  modules are sold by Quote, so the portal must not change or cancel the
  subscription.
- **Plus** consumers manage their plan (monthly or yearly), payment method and
  cancellation themselves.

Stripe's default portal configuration is one set of features for every
customer, so each kind gets its own configuration, declared here and applied
by ``scripts/stripe_portal.py`` like the catalogue in ``catalog.py``. A
session then names its configuration by id (``Settings.
stripe_portal_configuration_enterprise`` / ``_plus``); an empty setting falls
back to Stripe's default, which is what every tenant got before.

The portal never creates a Stripe customer. A tenant without one gets a 409
from ``/billing/portal-session``, and ``plus_portal_params`` refuses an empty
customer id: a Free user has no customer until Checkout makes one (#61).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import structlog

from app.config import Settings
from app.models.tenant import Plan, Tenant
from app.services.billing import stripe_client

log = structlog.get_logger()

# Metadata key that marks each configuration this module owns, and its values.
PORTAL_KEY = "promptly_portal"
ENTERPRISE = "enterprise"
PLUS = "plus"
KINDS = (ENTERPRISE, PLUS)

# The setting each configuration's id goes into, as an environment variable.
SETTING_FOR_KIND = {
    ENTERPRISE: "STRIPE_PORTAL_CONFIGURATION_ENTERPRISE",
    PLUS: "STRIPE_PORTAL_CONFIGURATION_PLUS",
}

HEADLINES = {
    ENTERPRISE: "Invoices and payment details. Seats are changed in the Atlas.AI admin console.",
    PLUS: "Promptly Plus",
}

CANCELLATION_REASONS = [
    "too_expensive",
    "unused",
    "missing_features",
    "switched_service",
    "too_complex",
    "low_quality",
    "customer_service",
    "other",
]


class NoCustomerError(ValueError):
    """There is no Stripe customer to open the portal for."""


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------


def enterprise_features() -> dict:
    """Invoices, payment method and billing details; no subscription changes."""
    return {
        "invoice_history": {"enabled": True},
        "payment_method_update": {"enabled": True},
        # The VAT ID is billing detail the customer owns (reverse charge, #50).
        "customer_update": {
            "enabled": True,
            "allowed_updates": ["address", "email", "name", "tax_id"],
        },
        "subscription_update": {"enabled": False},
        "subscription_cancel": {"enabled": False},
    }


def plus_features(products: Mapping[str, list[str]]) -> dict:
    """Plan, payment method and cancellation for a consumer.

    ``products`` maps the Plus product id to the price ids a subscriber may
    switch between (monthly and yearly). With none, switching is off and the
    subscriber can still cancel and resubscribe.

    Cancellation takes effect at the end of the paid period, so nobody loses
    days they paid for. Consumers have no VAT ID to give.
    """
    switch: dict = {"enabled": False}
    if products:
        switch = {
            "enabled": True,
            "default_allowed_updates": ["price"],
            "products": [
                {"product": product, "prices": sorted(prices)}
                for product, prices in sorted(products.items())
            ],
            "proration_behavior": "create_prorations",
        }
    return {
        "invoice_history": {"enabled": True},
        "payment_method_update": {"enabled": True},
        "customer_update": {"enabled": True, "allowed_updates": ["address", "email", "name"]},
        "subscription_update": switch,
        "subscription_cancel": {
            "enabled": True,
            "mode": "at_period_end",
            "cancellation_reason": {"enabled": True, "options": CANCELLATION_REASONS},
        },
    }


def plus_products(price_ids: list[str]) -> dict[str, list[str]]:
    """Group the configured Plus price ids by their Stripe product."""
    products: dict[str, list[str]] = {}
    for price_id in dict.fromkeys(p for p in price_ids if p):
        price = stripe_client.retrieve_price(price_id)
        product = price.get("product")
        product_id = product.get("id") if isinstance(product, Mapping) else product
        products.setdefault(product_id, []).append(price_id)
    return products


def configuration_params(kind: str, features: dict) -> dict:
    return {
        "business_profile": {"headline": HEADLINES[kind]},
        "features": features,
        "metadata": {PORTAL_KEY: kind},
    }


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action:
    verb: str  # "create", "update", "ok"
    kind: str  # ENTERPRISE or PLUS
    configuration_id: str | None
    detail: str = ""

    def __str__(self) -> str:
        tail = f" ({self.detail})" if self.detail else ""
        shown = self.configuration_id or "(new)"
        return f"{self.verb:<8} portal   {self.kind:<10} {shown}{tail}"


def _contains(have: object, want: object) -> bool:
    """True if ``have`` (as Stripe returns it) holds everything in ``want``.

    Stripe echoes every feature with fields we do not set, so only the fields
    we set are compared. Lists must match in order and length.
    """
    if isinstance(want, Mapping):
        if not isinstance(have, Mapping):
            return False
        return all(_contains(have.get(k), v) for k, v in want.items())
    if isinstance(want, list):
        if not isinstance(have, list) or len(have) != len(want):
            return False
        return all(_contains(h, w) for h, w in zip(have, want, strict=True))
    return have == want


def _find(configurations: list, kind: str):
    for conf in configurations:
        if (conf.get("metadata") or {}).get(PORTAL_KEY) == kind:
            return conf
    return None


def _sync_one(configurations: list, kind: str, features: dict, *, dry_run: bool) -> Action:
    params = configuration_params(kind, features)
    existing = _find(configurations, kind)
    if existing is None:
        conf_id = None
        if not dry_run:
            conf_id = stripe_client.create_portal_configuration(**params)["id"]
        return Action("create", kind, conf_id)

    changed = [
        k for k in ("business_profile", "features") if not _contains(existing.get(k), params[k])
    ]
    if existing.get("active") is False:
        changed.append("active")
    if not changed:
        return Action("ok", kind, existing["id"])
    if not dry_run:
        stripe_client.modify_portal_configuration(existing["id"], active=True, **params)
    return Action("update", kind, existing["id"], ", ".join(changed))


def sync_portal_configurations(
    plus_price_ids: list[str], *, dry_run: bool = False, offline: bool = False
) -> list[Action]:
    """Create or update the enterprise and Plus configurations; safe to repeat.

    Each is found by its ``promptly_portal`` metadata, so a second run makes
    no changes. ``offline`` plans against an empty account without calling
    Stripe (a dry run with no key), so Plus price ids are taken as given.
    """
    if offline:
        products = (
            {"(plus product)": [p for p in plus_price_ids if p]} if any(plus_price_ids) else {}
        )
        configurations: list = []
        dry_run = True
    else:
        products = plus_products(plus_price_ids)
        configurations = stripe_client.list_portal_configurations()

    actions = [
        _sync_one(configurations, ENTERPRISE, enterprise_features(), dry_run=dry_run),
        _sync_one(configurations, PLUS, plus_features(products), dry_run=dry_run),
    ]
    for action in actions:
        log.info("stripe_portal_action", action=str(action), dry_run=dry_run)
    return actions


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def is_enterprise(tenant: Tenant) -> bool:
    """A tenant with any Promptly enterprise or module subscription, or on Enterprise."""
    return tenant.plan is Plan.ENTERPRISE or bool(tenant.stripe_subscriptions)


def tenant_portal_params(tenant: Tenant, settings: Settings) -> dict:
    """Params for ``stripe.billing_portal.Session.create`` for a tenant.

    The caller has checked that the tenant has a Stripe customer. Enterprise
    tenants get the enterprise configuration; a self-serve Team tenant keeps
    Stripe's default, as before.
    """
    params: dict = {
        "customer": tenant.stripe_customer_id,
        "return_url": settings.billing_portal_return_url,
    }
    if is_enterprise(tenant):
        if settings.stripe_portal_configuration_enterprise:
            params["configuration"] = settings.stripe_portal_configuration_enterprise
        else:
            # Stripe's default may let the customer change seats or cancel.
            log.warning("stripe_portal_enterprise_configuration_unset", tenant_id=str(tenant.id))
    return params


def plus_portal_params(
    *, customer_id: str | None, return_url: str, configuration: str = ""
) -> dict:
    """Params for a Plus subscriber's portal session. No I/O.

    Keyed on the Stripe customer that Plus Checkout created, which the
    Personal account stores (promptly-guide#63). An empty id raises
    ``NoCustomerError`` instead of creating a customer: a Free user has
    nothing to manage, and should be sent to Checkout.
    """
    if not customer_id:
        raise NoCustomerError("no Stripe customer: upgrade through Plus Checkout first")
    params: dict = {"customer": customer_id, "return_url": return_url}
    if configuration:
        params["configuration"] = configuration
    return params


def create_plus_portal_session(customer_id: str | None, settings: Settings) -> str:
    """Open the portal for a Plus subscriber and return its URL.

    The one call a Personal account endpoint needs, wherever accounts end up
    (promptly-guide#63, decision 3).
    """
    params = plus_portal_params(
        customer_id=customer_id,
        return_url=settings.billing_portal_return_url_plus,
        configuration=settings.stripe_portal_configuration_plus,
    )
    return stripe_client.create_portal_session(**params).url
